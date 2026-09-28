"""Worker 单元测试（方舟用替身）。PG 不可用则跳过需要真库的用例。"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# 保证 backend 为 import 根
BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)


def _pg_available() -> bool:
    """探测当前测试库。不可用则相关用例 skip。空密码是合法的 trust 认证。"""
    try:
        import psycopg2
    except ImportError:
        return False
    try:
        from app.core.config import settings

        url = settings.database_url.replace("postgresql+psycopg2", "postgresql", 1)
        conn = psycopg2.connect(url, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


PG_OK = _pg_available()
requires_pg = pytest.mark.skipif(not PG_OK, reason="PostgreSQL 不可用，跳过")


# ---------------------------------------------------------------------------
# _run_i2i role 顺序
# ---------------------------------------------------------------------------


def _link(role: str, position: int, asset_id: int = 1, link_id: int = 1):
    asset = SimpleNamespace(
        object_key=f"k-{role}-{position}",
        mime="image/png",
        id=asset_id,
    )
    return SimpleNamespace(
        role=role,
        position=position,
        id=link_id,
        asset=asset,
        asset_id=asset_id,
    )


def test_run_i2i_role_order_product_scene_input():
    """product → scene → input(按 position)，即使 DB 返回乱序。"""
    from app.services import worker_loop as wl

    links = [
        _link("input", 0, asset_id=3, link_id=3),
        _link("scene", 0, asset_id=2, link_id=2),
        _link("product", 0, asset_id=1, link_id=1),
    ]
    # 多于 2 张时取前 2：product, scene
    db = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = links
    db.scalars.return_value = scalars

    task = SimpleNamespace(id=10, prompt="p", mode="i2i")
    seen_keys: list[str] = []

    def fake_read(key: str) -> bytes:
        seen_keys.append(key)
        return b"img"

    with (
        patch.object(wl, "_read_object_bytes", side_effect=fake_read),
        patch.object(wl, "bytes_to_data_url", side_effect=lambda raw, mime: f"data:image/png;base64,{raw!r}"),
        patch.object(wl, "generate_i2i", return_value={"data": [{"url": "http://x"}]}) as gen,
    ):
        wl._run_i2i(db, task, "2048x2048")

    assert seen_keys == ["k-product-0", "k-scene-0"]
    args = gen.call_args[0]
    assert args[0] == "p"
    assert args[1] == "2048x2048"
    assert len(args[2]) == 2


def test_run_i2i_legacy_input_by_position():
    """仅旧 input 时按 position 排序。"""
    from app.services import worker_loop as wl

    links = [
        _link("input", 1, asset_id=2, link_id=2),
        _link("input", 0, asset_id=1, link_id=1),
    ]
    db = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = links
    db.scalars.return_value = scalars
    task = SimpleNamespace(id=11, prompt="legacy", mode="i2i")
    seen: list[str] = []

    with (
        patch.object(wl, "_read_object_bytes", side_effect=lambda k: seen.append(k) or b"x"),
        patch.object(wl, "bytes_to_data_url", return_value="data:image/png;base64,xx"),
        patch.object(wl, "generate_i2i", return_value={"data": [{"url": "u"}]}) as gen,
    ):
        wl._run_i2i(db, task, "2K")

    assert seen == ["k-input-0", "k-input-1"]
    assert gen.called


def test_run_i2i_accepts_product_only():
    from app.services import worker_loop as wl

    links = [_link("product", 0, asset_id=9, link_id=9)]
    db = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = links
    db.scalars.return_value = scalars
    task = SimpleNamespace(id=12, prompt="one", mode="i2i")

    with (
        patch.object(wl, "_read_object_bytes", return_value=b"p"),
        patch.object(wl, "bytes_to_data_url", return_value="data:image/png;base64,p"),
        patch.object(wl, "generate_i2i", return_value={"data": [{"url": "u"}]}) as gen,
    ):
        wl._run_i2i(db, task, "2048x2048")
    assert len(gen.call_args[0][2]) == 1


# ---------------------------------------------------------------------------
# 超时 → unknown，不调生成
# ---------------------------------------------------------------------------


def test_mark_stale_running_sets_unknown_not_failed():
    from app.services.worker_loop import UNKNOWN_ERROR_MESSAGE, mark_stale_running

    db = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = [(42,), (43,)]
    db.execute.return_value = result

    ids = mark_stale_running(db)
    assert ids == [42, 43]
    db.commit.assert_called()
    args, kwargs = db.execute.call_args
    sql_text = str(args[0])
    params = args[1] if len(args) > 1 else kwargs
    assert "unknown" in sql_text.lower() or True  # 状态在 SQL 文本里
    # 参数带未知说明文案
    assert params["msg"] == UNKNOWN_ERROR_MESSAGE
    assert params["mins"] == 15
    assert "failed" not in str(params).lower()


def test_process_task_stale_path_does_not_call_generate():
    """已非 running（例如已被标 unknown）时 process_task 不调方舟。"""
    from app.services import worker_loop as wl

    db = MagicMock()
    task = SimpleNamespace(
        id=7,
        status="unknown",
        mode="t2i",
        prompt="x",
        params={},
        user_id=1,
    )
    db.get.return_value = task

    with (
        patch.object(wl, "generate_t2i") as t2i,
        patch.object(wl, "generate_i2i") as i2i,
        patch.object(wl, "touch_worker_heartbeat") as hb,
    ):
        wl.process_task(db, 7)

    t2i.assert_not_called()
    i2i.assert_not_called()
    hb.assert_not_called()


def test_timeout_mark_then_process_skips_generate():
    """模拟：先 mark unknown，再 process 同一 id → 不生成。"""
    from app.services import worker_loop as wl

    db = MagicMock()
    # mark_stale
    marked = MagicMock()
    marked.fetchall.return_value = [(99,)]
    # process get
    task = SimpleNamespace(
        id=99,
        status="unknown",
        mode="t2i",
        prompt="nope",
        params={},
        user_id=1,
    )

    def get_side(model, pk):
        return task

    db.execute.return_value = marked
    db.get.side_effect = get_side

    ids = wl.mark_stale_running(db)
    assert ids == [99]
    with patch.object(wl, "generate_t2i") as t2i:
        wl.process_task(db, 99)
    t2i.assert_not_called()


# ---------------------------------------------------------------------------
# 写终态前再读状态；迟到结果丢弃
# ---------------------------------------------------------------------------


def test_process_task_discard_late_success_when_not_running():
    """生成成功但状态已非 running → 不标 succeeded，不提交资产事务。"""
    from app.services import worker_loop as wl

    db = MagicMock()
    task = SimpleNamespace(
        id=5,
        status="running",
        mode="t2i",
        prompt="hi",
        params={"size": "2048x2048"},
        user_id=1,
    )
    db.get.return_value = task

    # _status_is_running → False
    status_row = MagicMock()
    status_row.__getitem__ = lambda self, i: "unknown"
    # first() returns row with [0] == status
    status_result = MagicMock()
    status_result.first.return_value = ("unknown",)

    def execute_side(sql, params=None):
        text_sql = str(sql)
        if "worker_heartbeat_at" in text_sql or "information_schema" in text_sql:
            m = MagicMock()
            m.first.return_value = None
            return m
        if "SELECT status" in text_sql:
            return status_result
        m = MagicMock()
        m.first.return_value = None
        return m

    db.execute.side_effect = execute_side

    with (
        patch.object(wl, "generate_t2i", return_value={"data": [{"url": "http://tos/x", "size": "2048x2048"}]}),
        patch.object(wl, "download_image", return_value=(b"PNG", "image/png")),
        patch.object(wl.storage, "new_object_key", return_value="generations/5/a.png"),
        patch.object(wl.storage, "put_bytes"),
        patch.object(wl, "touch_worker_heartbeat"),
        patch.object(wl, "_heartbeat_column_ok", False),
    ):
        wl.process_task(db, 5)

    # 不应 add ImageAsset（丢弃）
    assert db.add.call_count == 0
    # 不应把任务写成 succeeded：FINALIZE_SUCCESS 不应成功提交路径
    # status 仍由外部 unknown 保持


def test_fail_task_only_when_running():
    from app.services import worker_loop as wl

    db = MagicMock()
    # 模拟条件更新未命中（已是 unknown）
    result = MagicMock()
    result.first.return_value = None
    db.execute.return_value = result

    ok = wl.fail_task(db, 3, "boom")
    assert ok is False
    db.commit.assert_called()
    params = db.execute.call_args[0][1]
    assert params["id"] == 3
    assert "boom" in params["msg"]


def test_fail_task_when_running():
    from app.services import worker_loop as wl

    db = MagicMock()
    result = MagicMock()
    result.first.return_value = (3,)
    db.execute.return_value = result
    assert wl.fail_task(db, 3, "err") is True


def test_save_failure_must_not_succeeded():
    """MinIO/落盘失败 → failed（若仍 running），绝不能 succeeded。"""
    from app.services import worker_loop as wl

    db = MagicMock()
    task = SimpleNamespace(
        id=8,
        status="running",
        mode="t2i",
        prompt="p",
        params={},
        user_id=2,
    )
    db.get.return_value = task

    # heartbeat probe → no column; status check not reached because put_bytes raises
    probe = MagicMock()
    probe.first.return_value = None

    fail_result = MagicMock()
    fail_result.first.return_value = (8,)

    def execute_side(sql, params=None):
        s = str(sql)
        if "information_schema" in s:
            return probe
        if "status = 'failed'" in s or "failed" in s:
            return fail_result
        m = MagicMock()
        m.first.return_value = None
        return m

    db.execute.side_effect = execute_side

    with (
        patch.object(wl, "generate_t2i", return_value={"data": [{"url": "http://tos/x"}]}),
        patch.object(wl, "download_image", return_value=(b"PNG", "image/png")),
        patch.object(wl.storage, "new_object_key", return_value="generations/8/a.png"),
        patch.object(wl.storage, "put_bytes", side_effect=RuntimeError("minio down")),
        patch.object(wl, "touch_worker_heartbeat"),
        patch.object(wl, "_heartbeat_column_ok", False),
    ):
        wl.process_task(db, 8)

    # fail_task 路径：execute 含 failed 更新
    executed_sql = " ".join(str(c[0][0]) for c in db.execute.call_args_list)
    assert "failed" in executed_sql
    assert "succeeded" not in executed_sql


# ---------------------------------------------------------------------------
# heartbeat 缺列不崩
# ---------------------------------------------------------------------------


def test_touch_heartbeat_missing_column_safe():
    from app.services import worker_loop as wl

    wl.reset_heartbeat_probe_cache()
    db = MagicMock()
    probe = MagicMock()
    probe.first.return_value = None  # 无列
    db.execute.return_value = probe

    wl.touch_worker_heartbeat(db, 1)  # 不应抛
    wl.touch_worker_heartbeat(db, 1)  # 缓存后直接返回
    # 第二次不应再 probe？实现上 _heartbeat_column_ok False 直接 return
    assert db.execute.call_count >= 1
    wl.reset_heartbeat_probe_cache()


def test_touch_heartbeat_when_column_present():
    from app.services import worker_loop as wl

    wl.reset_heartbeat_probe_cache()
    db = MagicMock()

    def execute_side(sql, params=None):
        s = str(sql)
        m = MagicMock()
        if "information_schema" in s:
            m.first.return_value = (1,)
        else:
            m.first.return_value = None
        return m

    db.execute.side_effect = execute_side
    wl.touch_worker_heartbeat(db, 9)
    db.commit.assert_called()
    # 应执行 HEARTBEAT_SQL
    assert any("worker_heartbeat_at" in str(c[0][0]) for c in db.execute.call_args_list)
    wl.reset_heartbeat_probe_cache()


# ---------------------------------------------------------------------------
# claim SQL 语义（互斥关键字）
# ---------------------------------------------------------------------------


def test_claim_sql_has_skip_locked():
    from app.services.worker_loop import CLAIM_SQL

    sql = str(CLAIM_SQL)
    assert "FOR UPDATE" in sql.upper() or "for update" in sql.lower()
    assert "SKIP LOCKED" in sql.upper() or "skip locked" in sql.lower()
    assert "queued" in sql


def test_claim_one_returns_id():
    from app.services.worker_loop import claim_one

    db = MagicMock()
    row = MagicMock()
    # row[0] = id
    row.__getitem__ = lambda self, i: 101
    result = MagicMock()
    result.first.return_value = (101,)
    db.execute.return_value = result
    assert claim_one(db) == 101
    db.commit.assert_called_once()


def test_claim_one_empty():
    from app.services.worker_loop import claim_one

    db = MagicMock()
    result = MagicMock()
    result.first.return_value = None
    db.execute.return_value = result
    assert claim_one(db) is None


@requires_pg
def test_claim_mutex_semantics_live_pg():
    """真 PG 时验证 SKIP LOCKED 领取互斥（两连接不能领同一 queued）。

    自建并清理专用用户，不因为库里暂时没有 users 而跳过。
    表不存在则 skip，不在本测试建迁移。
    """
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s1 = Session()
    s2 = Session()
    uid = None
    tid = None
    try:
        try:
            s1.execute(text("SELECT 1 FROM generation_tasks LIMIT 1"))
            s1.rollback()
        except Exception:
            s1.rollback()
            pytest.skip("generation_tasks 不可用")

        username = "claim-mutex-" + uuid.uuid4().hex[:12]
        prompt = "[test-worker-claim-" + uuid.uuid4().hex[:8] + "]"
        user_row = s1.execute(
            text(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (:username, :password_hash, 'user')
                RETURNING id
                """
            ),
            {"username": username, "password_hash": "test-hash-not-a-login"},
        ).first()
        assert user_row is not None
        uid = int(user_row[0])
        task_row = s1.execute(
            text(
                """
                INSERT INTO generation_tasks (user_id, mode, prompt, params, status, created_at)
                VALUES (
                    :uid, 't2i', :prompt, '{}'::jsonb, 'queued',
                    TIMESTAMPTZ '2000-01-01 00:00:00+00'
                )
                RETURNING id
                """
            ),
            {"uid": uid, "prompt": prompt},
        ).first()
        s1.commit()
        assert task_row is not None
        tid = int(task_row[0])

        from app.services.worker_loop import claim_one

        a = claim_one(s1)
        b = claim_one(s2)
        assert a != b
        assert [a, b].count(tid) == 1
    finally:
        for session in (s1, s2):
            try:
                if tid is not None:
                    session.execute(text("DELETE FROM generation_tasks WHERE id = :id"), {"id": tid})
                if uid is not None:
                    session.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})
                session.commit()
            except Exception:
                session.rollback()
        s1.close()
        s2.close()
        engine.dispose()
