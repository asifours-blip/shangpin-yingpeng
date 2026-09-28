"""流水线工人。只领依赖已成功的步骤，不向自己的 HTTP 发请求。

领取时发令牌并预占预算。租约失效后，迟到结果不能改状态。
provider_request_id 只放上游任务号，不放本地请求号。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.campaign import Campaign, CampaignRun, ContentVariant, PipelineStep, VariantAsset
from app.models.copywriting import CopywritingOperation
from app.models.product import OwnedProduct, ProductFactVersion
from app.services.ark import bytes_to_data_url
from app.services.campaign_pipeline import text_asserts_unconfirmed
from app.services.consumer_qc import (
    COPY_REWRITE_LIMIT,
    plan_xiaohongshu,
    screen_consumer_copy,
    screen_text,
)
from app.services.copywriting_generator import produce_copy, sanitize_error
from app.services.media_render import (
    RenderError,
    publishable_lines,
    render_cover,
    render_planned_cards,
    render_story_clip,
    review_notes,
)
from app.services import storage

logger = logging.getLogger("pipeline_worker")

COPY_STEPS = ("copy:douyin", "copy:xiaohongshu")
MEDIA_STEPS = ("image:douyin", "video:douyin", "cards:xiaohongshu")
WORKER_STEPS = ("brief", *COPY_STEPS, *MEDIA_STEPS)
lease_seconds: float = 120
heartbeat_interval: float | None = 20

CLAIM_SQL = text(
    """
    WITH candidate AS (
        SELECT s.id, s.run_id, s.step_key
        FROM pipeline_steps s
        JOIN campaign_runs r ON r.id = s.run_id
        JOIN campaigns c ON c.id = r.campaign_id
        WHERE c.status = 'generating'
          AND s.status = 'pending'
          AND (:model_configured OR s.step_key NOT IN ('copy:douyin', 'copy:xiaohongshu'))
          AND s.step_key IN (
              'brief', 'copy:douyin', 'copy:xiaohongshu',
              'image:douyin', 'video:douyin', 'cards:xiaohongshu'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(COALESCE(s.depends_on, '[]'::jsonb)) AS dep(step_key)
              WHERE NOT EXISTS (
                  SELECT 1
                  FROM pipeline_steps parent
                  WHERE parent.run_id = s.run_id
                    AND parent.step_key = dep.step_key
                    AND parent.status = 'succeeded'
                    AND parent.version = (
                        SELECT MAX(older.version)
                        FROM pipeline_steps older
                        WHERE older.run_id = s.run_id
                          AND older.step_key = dep.step_key
                          AND older.version <= s.version
                    )
              )
          )
        ORDER BY s.id
        FOR UPDATE OF s SKIP LOCKED
        LIMIT 1
    ),
    reserve AS (
        UPDATE campaign_runs AS run
        SET budget_reserved = run.budget_reserved + 1
        FROM candidate
        WHERE run.id = candidate.run_id
          AND candidate.step_key IN ('copy:douyin', 'copy:xiaohongshu')
          AND run.budget_reserved < run.generation_budget
        RETURNING run.id
    )
    UPDATE pipeline_steps AS step
    SET status = CASE
            WHEN candidate.step_key NOT IN ('copy:douyin', 'copy:xiaohongshu')
              OR EXISTS (SELECT 1 FROM reserve)
            THEN 'running' ELSE 'skipped' END,
        error_code = CASE
            WHEN candidate.step_key IN ('copy:douyin', 'copy:xiaohongshu')
             AND NOT EXISTS (SELECT 1 FROM reserve)
            THEN 'budget_exceeded' ELSE step.error_code END,
        claim_token = CASE
            WHEN candidate.step_key NOT IN ('copy:douyin', 'copy:xiaohongshu')
              OR EXISTS (SELECT 1 FROM reserve)
            THEN md5(random()::text || clock_timestamp()::text) ELSE NULL END,
        local_request_id = CASE
            WHEN candidate.step_key IN ('copy:douyin', 'copy:xiaohongshu')
             AND EXISTS (SELECT 1 FROM reserve)
            THEN COALESCE(step.local_request_id, md5(random()::text || 'intent' || clock_timestamp()::text))
            ELSE step.local_request_id END,
        attempt = CASE
            WHEN candidate.step_key NOT IN ('copy:douyin', 'copy:xiaohongshu')
              OR EXISTS (SELECT 1 FROM reserve)
            THEN step.attempt + 1 ELSE step.attempt END,
        lease_until = CASE
            WHEN candidate.step_key NOT IN ('copy:douyin', 'copy:xiaohongshu')
              OR EXISTS (SELECT 1 FROM reserve)
            THEN clock_timestamp() + make_interval(secs => CAST(:lease_seconds AS double precision)) ELSE NULL END,
        heartbeat_at = CASE
            WHEN candidate.step_key NOT IN ('copy:douyin', 'copy:xiaohongshu')
              OR EXISTS (SELECT 1 FROM reserve)
            THEN clock_timestamp() ELSE step.heartbeat_at END
    FROM candidate
    WHERE step.id = candidate.id
      AND step.status = 'pending'
    RETURNING step.id, step.claim_token, step.status, step.run_id
    """
)

MARK_UNACKED_SQL = text(
    """
    UPDATE pipeline_steps
    SET status = 'unknown',
        error_code = 'submit_unacked',
        claim_token = NULL,
        lease_until = NULL
    WHERE status = 'running'
      AND lease_until IS NOT NULL
      AND lease_until < clock_timestamp()
      AND provider_request_id IS NULL
    RETURNING id, run_id
    """
)

RENEW_SQL = text(
    """
    UPDATE pipeline_steps
    SET heartbeat_at = clock_timestamp(),
        lease_until = clock_timestamp() + make_interval(secs => CAST(:lease_seconds AS double precision))
    WHERE id = :step_id
      AND claim_token = :token
      AND status = 'running'
      AND lease_until IS NOT NULL
      AND lease_until > clock_timestamp()
    RETURNING id
    """
)

FINISH_SQL = text(
    """
    UPDATE pipeline_steps
    SET status = :status,
        output = CAST(:output AS jsonb),
        error_code = :error_code,
        lease_until = NULL
    WHERE id = :step_id
      AND claim_token = :token
      AND status = 'running'
      AND lease_until IS NOT NULL
      AND lease_until > clock_timestamp()
    RETURNING id
    """
)

RESERVE_REWRITE_SQL = text(
    """
    UPDATE campaign_runs
    SET budget_reserved = budget_reserved + 1
    WHERE id = :run_id
      AND budget_reserved < generation_budget
    RETURNING budget_reserved
    """
)


@dataclass(frozen=True)
class Claim:
    step_id: int
    token: str


def claim_pending(db: Session, *, commit: bool = True) -> Claim | None:
    row = db.execute(
        CLAIM_SQL,
        {"lease_seconds": lease_seconds, "model_configured": bool(settings.ARK_API_KEY)},
    ).first()
    if row is None:
        if commit:
            db.commit()
        return None
    step_id, token, status, run_id = int(row[0]), row[1], row[2], int(row[3])
    if status != "running" or not token:
        db.execute(
            text(
                """
                UPDATE campaigns
                SET status = 'blocked'
                WHERE id = (SELECT campaign_id FROM campaign_runs WHERE id = :run_id)
                """
            ),
            {"run_id": run_id},
        )
        _block_downstream(db, run_id)
        if commit:
            db.commit()
        return None
    if commit:
        db.commit()
    return Claim(step_id, token)


def renew_lease(db: Session, step_id: int, token: str, *, commit: bool = True) -> bool:
    """长任务续租。令牌不对、或租约已经失效，就不能续。"""
    row = db.execute(
        RENEW_SQL,
        {"step_id": step_id, "token": token, "lease_seconds": lease_seconds},
    ).first()
    if commit:
        db.commit()
    return row is not None


def finish_step(
    db: Session,
    step_id: int,
    token: str,
    *,
    status: str,
    output: dict,
    error_code: str | None = None,
) -> bool:
    """只有仍持有令牌、且租约还没过期的工人能写最终状态。"""
    row = db.execute(
        FINISH_SQL,
        {
            "step_id": step_id,
            "token": token,
            "status": status,
            "output": json.dumps(output, ensure_ascii=False),
            "error_code": error_code,
        },
    ).first()
    if row is None:
        db.rollback()
        return False
    db.commit()
    return True


def _latest_steps(steps: list[PipelineStep]) -> list[PipelineStep]:
    latest: dict[str, PipelineStep] = {}
    for step in steps:
        current = latest.get(step.step_key)
        if current is None or step.version > current.version:
            latest[step.step_key] = step
    return list(latest.values())


def _block_downstream(db: Session, run_id: int) -> None:
    db.expire_all()
    changed = True
    while changed:
        changed = False
        steps = list(db.query(PipelineStep).filter(PipelineStep.run_id == run_id).all())
        for step in steps:
            if step.status != "pending":
                continue
            blocked = False
            for name in list(step.depends_on or []):
                parents = [
                    item
                    for item in steps
                    if item.step_key == name and item.version <= step.version
                ]
                if not parents:
                    continue
                parent = max(parents, key=lambda item: item.version)
                if parent.status in {"failed", "unknown", "skipped"}:
                    blocked = True
                    break
            if not blocked:
                continue
            step.status = "skipped"
            step.error_code = "blocked_by_upstream"
            step.claim_token = None
            changed = True
    db.commit()


def _refresh_campaign(db: Session, run_id: int) -> None:
    db.expire_all()
    run = db.get(CampaignRun, run_id)
    if run is None:
        return
    campaign = db.get(Campaign, run.campaign_id)
    if campaign is None or campaign.status in {"blocked", "approved"}:
        return
    steps = _latest_steps(list(db.query(PipelineStep).filter(PipelineStep.run_id == run_id).all()))
    if any(step.status == "unknown" for step in steps):
        campaign.status = "blocked"
    elif any(step.status in {"pending", "running"} for step in steps):
        if campaign.status in {"draft", "needs_review", "failed"}:
            campaign.status = "generating"
    elif any(step.status == "failed" for step in steps):
        campaign.status = "failed"
    elif steps and all(step.status == "succeeded" for step in steps):
        campaign.status = "needs_review"
        run.finished_at = datetime.now(timezone.utc)
    db.commit()


def mark_unacked(db: Session) -> list[int]:
    rows = db.execute(MARK_UNACKED_SQL).fetchall()
    db.commit()
    ids = []
    for row in rows:
        ids.append(int(row[0]))
        _block_downstream(db, int(row[1]))
        _refresh_campaign(db, int(row[1]))
    return ids


def _write_variant(db: Session, step: PipelineStep, content) -> None:
    """只写这一步绑定的变体版本，不查询最新版本。"""
    if step.variant_id is None or content is None:
        return
    variant = db.get(ContentVariant, step.variant_id)
    if variant is None or variant.version != step.version:
        return
    variant.title = content.title if hasattr(content, "title") else content.get("title")
    variant.body = content.body if hasattr(content, "body") else content.get("body")
    tags = content.hashtags if hasattr(content, "hashtags") else content.get("hashtags") or []
    variant.hashtags = list(tags)
    if variant.status not in {"approved", "rejected"}:
        variant.status = "generated"


def reconcile_expired(db: Session) -> list[int]:
    """已有上游任务号的过期步骤只查已落库的记录，不重新调用模型。"""
    now = datetime.now(timezone.utc)
    steps = list(
        db.query(PipelineStep)
        .filter(
            PipelineStep.status == "running",
            PipelineStep.provider_request_id.is_not(None),
            PipelineStep.lease_until.is_not(None),
            PipelineStep.lease_until < now,
        )
        .all()
    )
    touched: list[int] = []
    for step in steps:
        touched.append(step.id)
        op = None
        if step.copywriting_operation_id:
            op = db.get(CopywritingOperation, step.copywriting_operation_id)
        run = db.get(CampaignRun, step.run_id)
        if op is not None and op.status == "succeeded" and op.generated_content:
            if run is not None:
                _write_variant(db, step, op.generated_content)
            step.status = "succeeded"
            step.lease_until = None
            step.claim_token = None
            step.error_code = None
            step.output = {"reconciled": True, "copywriting_operation_id": op.id}
        elif op is not None and op.status == "failed":
            step.status = "failed"
            step.error_code = "reconciled_failed"
            step.lease_until = None
            step.claim_token = None
        else:
            step.error_code = "reconcile_required"
            step.lease_until = now + timedelta(minutes=2)
            step.heartbeat_at = now
        db.commit()
        if step.status in {"failed", "succeeded"}:
            _block_downstream(db, step.run_id)
            _refresh_campaign(db, step.run_id)
    return touched


def _release_read(db: Session) -> None:
    """生成开始前结束读事务，避免 now() 停在事务起点。"""
    if db.in_transaction():
        db.commit()


def run_leased(step_id: int, token: str, work):
    """从生成到最终提交都用独立会话按实际时钟续租。

    work 收到 lost 事件。续租失败后不得提交成功或资产关联。
    线程会等到 work 返回（含最终提交）才停。
    """
    lost = threading.Event()
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(heartbeat_interval or 0):
            session = SessionLocal()
            try:
                ok = renew_lease(session, step_id, token)
            except Exception:
                lost.set()
                return
            finally:
                session.close()
            if not ok:
                lost.set()
                return

    thread = None
    if heartbeat_interval is not None and heartbeat_interval > 0:
        thread = threading.Thread(target=_beat, name="pipeline-heartbeat", daemon=True)
        thread.start()
    try:
        value = work(lost)
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=2)
    return value, lost.is_set()


def asset_data_url(db: Session, asset_id: int) -> str:
    from app.models import ImageAsset

    asset = db.get(ImageAsset, asset_id)
    if asset is None:
        raise ValueError("商品图不存在")
    raw = storage.get_bytes(asset.object_key)
    if not raw:
        raise ValueError("商品图为空")
    return bytes_to_data_url(raw, asset.mime)


def _fail_held(db: Session, step: PipelineStep, token: str, code: str, op: CopywritingOperation | None = None, message: str | None = None) -> bool:
    if op is not None and op.status == "processing":
        op.status = "failed"
        op.error_message = message or code
    held = finish_step(db, step.id, token, status="failed", output={"error": code}, error_code=code)
    if not held:
        return False
    _block_downstream(db, step.run_id)
    _refresh_campaign(db, step.run_id)
    return True


def _insert_uploaded(
    db: Session,
    *,
    owner_id: int,
    key: str,
    mime: str,
    size: int,
    width: int,
    height: int,
):
    from app.core.config import settings
    from app.models import ImageAsset

    asset = ImageAsset(
        owner_id=owner_id,
        bucket=settings.MINIO_BUCKET,
        object_key=key,
        mime=mime,
        size_bytes=size,
        width=width,
        height=height,
    )
    db.add(asset)
    db.flush()
    return asset


def _discard_upload(keys: list[str]) -> None:
    """本次上传已落对象存储、但资产关联没有提交时，删掉这些孤立对象。"""
    for key in keys:
        try:
            storage.delete_object(key)
        except Exception:
            logger.warning("未能删除孤立对象 %s", key)


def _attach(db: Session, variant: ContentVariant, asset_id: int, role: str, position: int) -> None:
    db.add(VariantAsset(variant_id=variant.id, asset_id=asset_id, role=role, position=position))


def _bound_variant(db: Session, step: PipelineStep) -> ContentVariant | None:
    if step.variant_id is None:
        return None
    return db.get(ContentVariant, step.variant_id)


def _held(step: PipelineStep, token: str) -> bool:
    return bool(token) and step.claim_token == token and step.status == "running"


def _process_media(db: Session, step: PipelineStep, campaign: Campaign, token: str) -> None:
    from app.models import ImageAsset

    if not renew_lease(db, step.id, token):
        return
    product = db.get(OwnedProduct, campaign.product_id)
    variant = _bound_variant(db, step)
    if variant is None or variant.version != step.version:
        _fail_held(db, step, token, "unbound_variant")
        return
    fact = db.get(ProductFactVersion, variant.fact_version_id or campaign.fact_version_id)
    if product is None or fact is None or product.primary_asset_id is None:
        _fail_held(db, step, token, "missing_asset")
        return
    source_asset = db.get(ImageAsset, product.primary_asset_id)
    if source_asset is None:
        _fail_held(db, step, token, "missing_asset")
        return
    db.refresh(step)
    if step.provider_request_id:
        return
    facts = dict(fact.facts or {})
    facts.setdefault("product_name", product.name)
    shown = publishable_lines(facts)
    notes = review_notes(facts)
    raw = storage.get_bytes(source_asset.object_key)
    if not raw:
        _fail_held(db, step, token, "media_failed", message="商品图为空")
        return
    bound_id = variant.id
    title = (variant.title if variant and variant.title else None) or product.name
    body = (variant.body if variant and variant.body else None) or ""
    tags = list(variant.hashtags or []) if variant is not None else []
    owner_id = campaign.owner_id
    campaign_id = campaign.id
    platform = step.variant_platform
    step_key = step.step_key
    run_id = step.run_id
    step_id = step.id
    db.commit()
    _release_read(db)
    uploaded: list[str] = []
    state = {"committed": False}

    def _render():
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            source.write_bytes(raw)
            if step_key == "image:douyin":
                dest = root / "cover.png"
                render_cover(source, dest, width=720, height=1280, title=title, lines=shown)
                return {"kind": "image", "data": dest.read_bytes()}
            if step_key == "video:douyin":
                dest = root / "final.mp4"
                info = render_story_clip(
                    source, dest, title=title, body=body, lines=shown, hashtags=tags, review=notes
                )
                return {"kind": "video", "data": dest.read_bytes(), "info": info}
            cards_plan = plan_xiaohongshu(facts, title=title, body=body)
            planned = render_planned_cards(source, root / "cards", cards_plan["cards"], review=notes)
            return {
                "kind": "cards",
                "blobs": [path.read_bytes() for path in planned["paths"]],
                "cards": planned,
                "plan": cards_plan,
            }

    def _parts(rendered: dict) -> list[dict]:
        if rendered["kind"] == "image":
            return [{
                "data": rendered["data"], "mime": "image/png", "ext": "png",
                "width": 720, "height": 1280, "role": "cover", "position": 0,
            }]
        if rendered["kind"] == "video":
            info = rendered["info"]
            return [{
                "data": rendered["data"], "mime": "video/mp4", "ext": "mp4",
                "width": int(info["width"]), "height": int(info["height"]),
                "role": "final_video", "position": 0, "info": info,
            }]
        return [
            {
                "data": blob, "mime": "image/png", "ext": "png",
                "width": 1080, "height": 1440,
                "role": "cover" if position == 0 else "card", "position": position,
            }
            for position, blob in enumerate(rendered["blobs"])
        ]

    def _work(lost: threading.Event):
        if step_key != "cards:xiaohongshu":
            issues = screen_consumer_copy(title=title, body=body, hashtags=tags, facts=facts)["issues"]
            for line in shown:
                issues.extend(screen_text(line, facts, field="fact_line"))
            if issues:
                if lost.is_set():
                    return
                state["committed"] = finish_step(
                    db,
                    step_id,
                    token,
                    status="failed",
                    output={"issues": issues, "action": "human_edit", "title": title, "body": body},
                    error_code=issues[0]["code"],
                )
                return
        rendered = _render()
        if lost.is_set() or rendered is None:
            return
        _release_read(db)
        parts = _parts(rendered)
        for part in parts:
            if lost.is_set():
                return
            key = storage.new_object_key("campaigns", part["ext"])
            uploaded.append(key)
            part["key"] = key
            storage.put_bytes(key, part["data"], part["mime"])
            if not storage.object_exists(key):
                raise RenderError("对象写入后仍不可读")
            if lost.is_set():
                return
        if lost.is_set():
            return
        variant = db.get(ContentVariant, bound_id)
        if rendered["kind"] == "image":
            asset = _insert_uploaded(
                db, owner_id=owner_id, key=parts[0]["key"], mime="image/png",
                size=len(parts[0]["data"]), width=720, height=1280,
            )
            if variant is not None:
                _attach(db, variant, asset.id, "cover", 0)
            output = {"asset_id": asset.id, "role": "cover", "title": title, "lines": shown, "review_notes": notes}
            step_status = "succeeded"
            step_error = None
        elif rendered["kind"] == "video":
            info = parts[0]["info"]
            asset = _insert_uploaded(
                db, owner_id=owner_id, key=parts[0]["key"], mime="video/mp4",
                size=len(parts[0]["data"]), width=int(info["width"]), height=int(info["height"]),
            )
            if variant is not None:
                _attach(db, variant, asset.id, "final_video", 0)
                variant.qc_result = {
                    "status": info["qc"],
                    "reason": info["qc_reason"],
                    "qc_issue": info.get("qc_issue"),
                    "unplaced": info.get("unplaced") or "",
                    "review_notes": notes,
                    "probe": {
                        key: info.get(key)
                        for key in ("tool", "codec", "audio_codec", "audio_source", "width", "height", "duration", "native_model_video")
                    },
                }
                variant.status = "needs_review"
                variant.storyboard = {"title": title, "body": body, "lines": shown, "hashtags": tags}
            output = {
                "asset_id": asset.id,
                "probe": info,
                "title": title,
                "body": body,
                "lines": list(info.get("lines") or shown),
                "review_notes": notes,
                "qc_issue": info.get("qc_issue"),
                "unplaced": info.get("unplaced") or "",
            }
            step_status = "succeeded"
            step_error = None
        else:
            cards = rendered["cards"]
            output_ids = []
            for part in parts:
                asset = _insert_uploaded(
                    db, owner_id=owner_id, key=part["key"], mime="image/png",
                    size=len(part["data"]), width=1080, height=1440,
                )
                if variant is not None:
                    _attach(db, variant, asset.id, part["role"], part["position"])
                output_ids.append(asset.id)
            if variant is not None:
                variant.status = "blocked" if rendered["plan"]["code"] else "needs_review"
                variant.qc_result = {
                    "card_count": len(output_ids),
                    "ordered": True,
                    "lines": cards["lines"],
                    "purposes": [card["purpose"] for card in rendered["plan"]["cards"]],
                    "qc_issue": rendered["plan"]["code"],
                    "missing": rendered["plan"]["missing"],
                    "supplements_required": rendered["plan"].get("supplements_required", False),
                    "need_more_cards": rendered["plan"].get("need_more_cards", 0),
                    "cards": rendered["plan"]["cards"],
                    "review_notes": notes,
                    "unplaced": cards["unplaced"],
                }
            output = {
                "asset_ids": output_ids,
                "title": title,
                "lines": cards["lines"],
                "review_notes": notes,
                "cards": rendered["plan"]["cards"],
                "missing": rendered["plan"]["missing"],
                "supplements_required": rendered["plan"].get("supplements_required", False),
                "need_more_cards": rendered["plan"].get("need_more_cards", 0),
                "qc_issue": rendered["plan"]["code"],
                "unplaced": cards["unplaced"],
            }
            step_status = "failed" if rendered["plan"]["code"] else "succeeded"
            step_error = rendered["plan"]["code"]
        if lost.is_set():
            return
        state["committed"] = finish_step(
            db, step_id, token, status=step_status, output=output, error_code=step_error
        )

    try:
        run_leased(step_id, token, _work)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        _discard_upload(uploaded)
        _fail_held(db, step, token, "media_failed", message=str(exc)[:300])
        return
    if not state["committed"]:
        db.rollback()
        _discard_upload(uploaded)
        return
    _refresh_campaign(db, run_id)


def process_step(db: Session, step_id: int, token: str) -> None:
    step = db.get(PipelineStep, step_id)
    if step is None or not _held(step, token):
        return
    if step.provider_request_id:
        return
    if not renew_lease(db, step_id, token):
        return
    run = db.get(CampaignRun, step.run_id)
    if run is None:
        return
    campaign = db.get(Campaign, run.campaign_id)
    if campaign is None:
        return
    if step.step_key == "brief":
        if finish_step(
            db,
            step_id,
            token,
            status="succeeded",
            output={"brief": campaign.brief, "source_item_ids": campaign.selected_source_item_ids},
        ):
            _refresh_campaign(db, run.id)
        return
    if step.step_key in MEDIA_STEPS:
        _process_media(db, step, campaign, token)
        return
    if step.step_key not in COPY_STEPS:
        return
    product = db.get(OwnedProduct, campaign.product_id)
    variant = _bound_variant(db, step)
    if variant is None or variant.version != step.version:
        _fail_held(db, step, token, "unbound_variant")
        return
    fact = db.get(ProductFactVersion, variant.fact_version_id or campaign.fact_version_id)
    if product is None or fact is None or product.primary_asset_id is None:
        _fail_held(db, step, token, "missing_asset")
        return
    db.refresh(step)
    if not step.local_request_id or step.local_request_id == step.provider_request_id:
        _fail_held(db, step, token, "missing_local_intent")
        return
    facts = dict(fact.facts or {})
    facts.setdefault("product_name", product.name)
    generation_requirements = str((campaign.brief or {}).get("generation_requirements") or "").strip()
    source_references = list((campaign.brief or {}).get("execute") or [])
    op = CopywritingOperation(
        user_id=campaign.owner_id,
        input_asset_id=product.primary_asset_id,
        platform=step.variant_platform,
        product_facts=facts,
        status="processing",
    )
    db.add(op)
    db.flush()
    step.copywriting_operation_id = op.id
    db.commit()
    db.refresh(step)
    if not _held(step, token):
        return
    try:
        data_url = asset_data_url(db, product.primary_asset_id)
        db.commit()
        _release_read(db)
        drafts: list[dict] = []
        accepted = None
        rewrites = 0
        while True:
            def _generate(_lost):
                kwargs = dict(
                    platform=step.variant_platform,
                    facts=facts,
                    data_url=data_url,
                )
                if generation_requirements:
                    kwargs["generation_requirements"] = generation_requirements
                if source_references:
                    kwargs["source_references"] = source_references
                return produce_copy(**kwargs)

            generated, lost = run_leased(step_id, token, _generate)
            if lost or generated is None:
                db.rollback()
                return
            content, risk = generated
            issues = screen_consumer_copy(
                title=content.title,
                body=content.body,
                hashtags=list(content.hashtags or []),
                facts=facts,
            )["issues"]
            drafts.append(
                {
                    "title": content.title,
                    "body": content.body,
                    "hashtags": list(content.hashtags or []),
                    "issues": issues,
                }
            )
            if not issues:
                accepted = (content, risk)
                break
            if rewrites >= COPY_REWRITE_LIMIT:
                break
            reserved = db.execute(RESERVE_REWRITE_SQL, {"run_id": run.id}).first()
            if reserved is None:
                db.rollback()
                break
            step.attempt = int(step.attempt or 0) + 1
            db.commit()
            _release_read(db)
            rewrites += 1
        if accepted is None:
            op.status = "failed"
            op.generated_content = None
            op.error_message = drafts[-1]["issues"][0]["reason"] if drafts and drafts[-1]["issues"] else "consumer_qc"
            code = drafts[-1]["issues"][0]["code"] if drafts and drafts[-1]["issues"] else "consumer_qc"
            if finish_step(
                db,
                step_id,
                token,
                status="failed",
                output={"drafts": drafts, "action": "human_edit", "rewrites": rewrites},
                error_code=code,
            ):
                _block_downstream(db, step.run_id)
                _refresh_campaign(db, run.id)
            return
        content, risk = accepted
        op.status = "succeeded"
        op.generated_content = content.model_dump()
        op.risk_result = risk.model_dump()
        op.error_message = None
        _write_variant(db, step, content)
        if not finish_step(
            db,
            step_id,
            token,
            status="succeeded",
            output={
                "copywriting_operation_id": op.id,
                "title": content.title,
                "rejected_drafts": [item for item in drafts if item["issues"]],
            },
        ):
            return
        _refresh_campaign(db, run.id)
    except Exception as exc:  # noqa: BLE001
        _fail_held(db, step, token, "copy_failed", op, sanitize_error(exc))


def tick(db: Session) -> int | None:
    mark_unacked(db)
    reconcile_expired(db)
    claim = claim_pending(db)
    if claim is None:
        return None
    process_step(db, claim.step_id, claim.token)
    return claim.step_id


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    while True:
        db = SessionLocal()
        try:
            step_id = tick(db)
            if step_id is None:
                time.sleep(2)
        finally:
            db.close()


if __name__ == "__main__":
    main()
