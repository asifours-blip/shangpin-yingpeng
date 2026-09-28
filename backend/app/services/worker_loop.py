"""Worker：数据库原子领取 queued 任务 → 真调方舟 → GET 落盘 MinIO → 写资产行。任一步失败不得标完成。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from minio.error import S3Error
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.minio_client import minio_client
from app.models import GenerationTask, GenerationTaskAsset, ImageAsset
from app.services import storage
from app.services.ark import (
    ArkError,
    bytes_to_data_url,
    create_i2v_task,
    download_image,
    generate_i2i,
    generate_t2i,
    wait_i2v_result,
)

# running 超过该分钟数 → unknown（不重投模型）
STALE_RUNNING_MINUTES = 15
UNKNOWN_ERROR_MESSAGE = "结果未知，未自动重发"

# 参考图 role 排序：商品 → 场景 → 旧 input（按 position）
_REF_ROLE_ORDER = {"product": 0, "scene": 1, "input": 2}

# worker_heartbeat_at 列由 A 迁移；探测一次后缓存，缺列不崩
_heartbeat_column_ok: bool | None = None

CLAIM_SQL = text(
    """
    WITH picked AS (
        SELECT id FROM generation_tasks
        WHERE status = 'queued'
        ORDER BY created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    UPDATE generation_tasks AS t
    SET status = 'running',
        started_at = NOW(),
        updated_at = NOW()
    FROM picked
    WHERE t.id = picked.id
    RETURNING t.id
    """
)

MARK_STALE_SQL = text(
    """
    UPDATE generation_tasks
    SET status = 'unknown',
        error_message = :msg,
        updated_at = NOW()
    WHERE status = 'running'
      AND started_at IS NOT NULL
      AND started_at < (NOW() - make_interval(mins => :mins))
    RETURNING id
    """
)

FINALIZE_SUCCESS_SQL = text(
    """
    UPDATE generation_tasks
    SET status = 'succeeded',
        error_message = NULL,
        finished_at = :finished_at,
        updated_at = :updated_at
    WHERE id = :id
      AND status = 'running'
    RETURNING id
    """
)

FINALIZE_FAILED_SQL = text(
    """
    UPDATE generation_tasks
    SET status = 'failed',
        error_message = :msg,
        finished_at = :finished_at,
        updated_at = :updated_at
    WHERE id = :id
      AND status = 'running'
    RETURNING id
    """
)

HEARTBEAT_SQL = text(
    """
    UPDATE generation_tasks
    SET worker_heartbeat_at = NOW(),
        updated_at = NOW()
    WHERE id = :id
      AND status = 'running'
    """
)

HEARTBEAT_COLUMN_PROBE_SQL = text(
    """
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'generation_tasks'
      AND column_name = 'worker_heartbeat_at'
    LIMIT 1
    """
)


def claim_one(db: Session) -> int | None:
    """原子领取一条 queued → running。SKIP LOCKED 保证多 worker 互斥。"""
    row = db.execute(CLAIM_SQL).first()
    db.commit()
    if row is None:
        return None
    return int(row[0])


def mark_stale_running(db: Session) -> list[int]:
    """把 running 且 started_at 超过 15 分钟的任务标为 unknown。

    不是 failed；不重投模型。error_message 说明结果未知、未自动重发。
    返回被标记的任务 id 列表。
    """
    rows = db.execute(
        MARK_STALE_SQL,
        {"msg": UNKNOWN_ERROR_MESSAGE, "mins": STALE_RUNNING_MINUTES},
    ).fetchall()
    db.commit()
    return [int(r[0]) for r in rows]


def process_task(db: Session, task_id: int) -> None:
    """处理已领取任务。写成功/失败前再读状态：仅仍为 running 才落终态。"""
    task = db.get(GenerationTask, task_id)
    if task is None:
        return
    if getattr(task, "status", None) != "running":
        # 已被标 unknown / 人工处理后不再执行
        return

    touch_worker_heartbeat(db, task_id)

    try:
        if task.mode == "i2v":
            raw, content_type = _run_i2v(db, task)
            ext = "mp4"
            width = height = None
            if "webm" in (content_type or ""):
                ext = "webm"
            elif "quicktime" in (content_type or "") or "mov" in (content_type or ""):
                ext = "mov"
        else:
            size = str((task.params or {}).get("size") or "2048x2048")
            if task.mode == "t2i":
                body = generate_t2i(task.prompt, size)
            elif task.mode == "i2i":
                body = _run_i2i(db, task, size)
            else:
                raise ArkError(f"不支持的 mode={task.mode}")

            touch_worker_heartbeat(db, task_id)

            tos_url = body["data"][0]["url"]
            raw, content_type = download_image(tos_url)
            ext = "png" if "png" in content_type else "jpg"
            width = height = None
            reported = body["data"][0].get("size")
            if isinstance(reported, str) and "x" in reported:
                try:
                    width, height = (int(x) for x in reported.lower().split("x", 1))
                except ValueError:
                    width = height = None

        touch_worker_heartbeat(db, task_id)

        object_key = storage.new_object_key(f"generations/{task.id}", ext)
        stored_mime = content_type or ("video/mp4" if task.mode == "i2v" else "image/png")
        if task.mode == "i2v" and not stored_mime.startswith("video/"):
            stored_mime = "video/mp4"
        storage.put_bytes(object_key, raw, stored_mime)

        # 写成功前再确认仍为 running；已被 unknown/人工处理则丢弃迟到结果
        if not _status_is_running(db, task_id):
            return

        asset = ImageAsset(
            owner_id=task.user_id,
            bucket=settings.MINIO_BUCKET,
            object_key=object_key,
            mime=stored_mime,
            size_bytes=len(raw),
            width=width,
            height=height,
        )
        db.add(asset)
        db.flush()
        db.add(
            GenerationTaskAsset(
                task_id=task.id,
                asset_id=asset.id,
                role="output",
                position=0,
            )
        )

        now = datetime.now(timezone.utc)
        # 条件更新：仅 running → succeeded，防止覆盖 unknown
        row = db.execute(
            FINALIZE_SUCCESS_SQL,
            {"id": task_id, "finished_at": now, "updated_at": now},
        ).first()
        if row is None:
            db.rollback()
            return
        db.commit()
    except Exception as exc:
        db.rollback()
        fail_task(db, task_id, str(exc))


def fail_task(db: Session, task_id: int, message: str) -> bool:
    """仅当仍为 running 时标 failed。已被 unknown/人工处理则丢弃。返回是否写入。"""
    now = datetime.now(timezone.utc)
    row = db.execute(
        FINALIZE_FAILED_SQL,
        {
            "id": task_id,
            "msg": (message or "")[:2000],
            "finished_at": now,
            "updated_at": now,
        },
    ).first()
    db.commit()
    return row is not None


def touch_worker_heartbeat(db: Session, task_id: int) -> None:
    """running 期间刷新 worker_heartbeat_at。缺列不崩，可测。"""
    global _heartbeat_column_ok
    if _heartbeat_column_ok is False:
        return
    if _heartbeat_column_ok is None:
        if not _probe_heartbeat_column(db):
            _heartbeat_column_ok = False
            return
        _heartbeat_column_ok = True
    try:
        db.execute(HEARTBEAT_SQL, {"id": task_id})
        db.commit()
    except ProgrammingError:
        db.rollback()
        _heartbeat_column_ok = False
    except SQLAlchemyError:
        db.rollback()
        # 其它 SQL 错误不永久禁用，下次可再试
        return


def reset_heartbeat_probe_cache() -> None:
    """测试用：重置缺列探测缓存。"""
    global _heartbeat_column_ok
    _heartbeat_column_ok = None


def _probe_heartbeat_column(db: Session) -> bool:
    try:
        row = db.execute(HEARTBEAT_COLUMN_PROBE_SQL).first()
        return row is not None
    except SQLAlchemyError:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        return False


def _status_is_running(db: Session, task_id: int) -> bool:
    """再读库内状态，避免 ORM 缓存。"""
    row = db.execute(
        text("SELECT status FROM generation_tasks WHERE id = :id"),
        {"id": task_id},
    ).first()
    return row is not None and row[0] == "running"


def _run_i2i(db: Session, task: GenerationTask, size: str) -> dict[str, Any]:
    """从 MinIO 读参考图 → Base64 → 调方舟。

    role 接受 product / scene / input；顺序：product → scene → 其余 input（按 position）。
    禁止把本机 MinIO URL 传给 Ark。
    """
    links = db.scalars(
        select(GenerationTaskAsset)
        .options(selectinload(GenerationTaskAsset.asset))
        .where(
            GenerationTaskAsset.task_id == task.id,
            GenerationTaskAsset.role.in_(("product", "scene", "input")),
        )
    ).all()

    ordered = sorted(
        links,
        key=lambda link: (
            _REF_ROLE_ORDER.get(link.role, 99),
            link.position if link.position is not None else 0,
            link.id or 0,
        ),
    )
    if not (1 <= len(ordered) <= 2):
        # 多于 2 张时仍按顺序取前 2 张（契约最多商品+场景）
        if len(ordered) > 2:
            ordered = ordered[:2]
        else:
            raise ArkError("参考图生图需要 1 或 2 张输入图")

    data_urls: list[str] = []
    for link in ordered:
        asset = link.asset
        if asset is None:
            raise ArkError("参考图资产缺失")
        raw = _read_object_bytes(asset.object_key)
        data_urls.append(bytes_to_data_url(raw, asset.mime))
    return generate_i2i(task.prompt, size, data_urls)


def _run_i2v(db: Session, task: GenerationTask) -> tuple[bytes, str]:
    """从 MinIO 读商品图 → 提交图生视频 → 轮询 → 下载 mp4。"""
    links = db.scalars(
        select(GenerationTaskAsset)
        .options(selectinload(GenerationTaskAsset.asset))
        .where(
            GenerationTaskAsset.task_id == task.id,
            GenerationTaskAsset.role.in_(("product", "input")),
        )
    ).all()
    ordered = sorted(
        links,
        key=lambda link: (
            _REF_ROLE_ORDER.get(link.role, 99),
            link.position if link.position is not None else 0,
            link.id or 0,
        ),
    )
    if not ordered:
        raise ArkError("图生视频需要 1 张商品图")
    asset = ordered[0].asset
    if asset is None:
        raise ArkError("参考图资产缺失")
    raw = _read_object_bytes(asset.object_key)
    data_url = bytes_to_data_url(raw, asset.mime)
    params = task.params or {}
    try:
        duration = int(params.get("duration") or 5)
    except (TypeError, ValueError):
        duration = 5
    resolution = str(params.get("resolution") or "480p")
    ratio = str(params.get("ratio") or "16:9")

    def on_tick() -> None:
        touch_worker_heartbeat(db, task.id)

    ark_task_id = create_i2v_task(
        task.prompt,
        data_url,
        duration=duration,
        resolution=resolution,
        ratio=ratio,
    )
    video_url = wait_i2v_result(ark_task_id, on_tick=on_tick)
    video_raw, content_type = download_image(video_url)
    return video_raw, content_type or "video/mp4"


def _read_object_bytes(object_key: str) -> bytes:
    try:
        resp = minio_client.get_object(settings.MINIO_BUCKET, object_key)
        try:
            data = resp.read()
        finally:
            resp.close()
            resp.release_conn()
    except S3Error as exc:
        raise ArkError(f"读取参考图失败：{exc}") from exc
    if not data:
        raise ArkError("参考图为空")
    return data
