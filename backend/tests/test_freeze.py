"""Wave 4：冻结 / 事务 / 申诉。隔离 ops_ 用户，不碰 demo/admin/任务 5，不调方舟。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import GenerationTask
from tests.conftest import as_user, cleanup, make_asset, make_user, pg_available

pytestmark = pytest.mark.skipif(not pg_available(), reason="PostgreSQL unavailable")

QUEUED_STOPPED = "用户被冻结，排队任务停止"


def _has_account_frozen(detail) -> bool:
    """403 detail 可能是对象 {code: ACCOUNT_FROZEN} 或字符串。"""
    if isinstance(detail, dict):
        return detail.get("code") == "ACCOUNT_FROZEN" or "ACCOUNT_FROZEN" in str(detail)
    return "ACCOUNT_FROZEN" in str(detail)


def _patch_freeze(client: TestClient, user_id: int, *, action: str, reason: str | None = "测试冻结"):
    payload: dict = {"action": action}
    if action == "freeze" or reason is not None:
        payload["reason"] = reason
    return client.patch(f"/api/admin/users/{user_id}/freeze", json=payload)


def _insert_queued(db, user) -> GenerationTask:
    task = GenerationTask(
        user_id=user.id,
        mode="t2i",
        prompt="ops freeze queued",
        params={"size": "2048x2048"},
        status="queued",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _done(db, *users) -> None:
    """API 在另一会话改了 current_freeze_event_id，清理前必须 expire。"""
    db.expire_all()
    cleanup(db, *users)


def test_frozen_user_generations_403_me_and_appeals_allowed(client: TestClient, db) -> None:
    admin = make_user(db, role="admin")
    user = make_user(db)
    try:
        as_user(admin)
        fr = _patch_freeze(client, user.id, action="freeze", reason="测试冻结")
        assert fr.status_code == 200, fr.text
        db.expire_all()

        as_user(user)
        blocked = client.get("/api/generations")
        assert blocked.status_code == 403
        assert _has_account_frozen(blocked.json().get("detail"))

        me = client.get("/api/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["is_frozen"] is True

        listed = client.get("/api/appeals")
        assert listed.status_code == 200, listed.text
        assert "items" in listed.json()

        posted = client.post("/api/appeals", json={"content": "申请解冻测试"})
        assert posted.status_code != 403
        assert posted.status_code == 201, posted.text
    finally:
        _done(db, admin, user)


def test_freeze_does_not_confuse_with_is_active(client: TestClient, db) -> None:
    """冻结走 403 ACCOUNT_FROZEN；is_active 仍 True，不与禁用混用。"""
    admin = make_user(db, role="admin")
    user = make_user(db)
    try:
        assert user.is_active is True
        assert user.is_frozen is False
        as_user(admin)
        fr = _patch_freeze(client, user.id, action="freeze", reason="测试冻结")
        assert fr.status_code == 200, fr.text
        body = fr.json()
        assert body["is_frozen"] is True
        assert body["is_active"] is True

        db.expire_all()
        as_user(user)
        blocked = client.get("/api/generations")
        assert blocked.status_code == 403
        assert _has_account_frozen(blocked.json().get("detail"))
        assert blocked.status_code != 401
    finally:
        _done(db, admin, user)


def test_admin_cannot_freeze_self_or_admin_role(client: TestClient, db) -> None:
    admin = make_user(db, role="admin")
    other_admin = make_user(db, role="admin")
    try:
        as_user(admin)
        self_r = _patch_freeze(client, admin.id, action="freeze", reason="测试冻结")
        assert self_r.status_code == 400
        assert "自己" in str(self_r.json().get("detail"))

        admin_r = _patch_freeze(client, other_admin.id, action="freeze", reason="测试冻结")
        assert admin_r.status_code == 400
        detail = str(admin_r.json().get("detail"))
        assert "管理" in detail or "演示" in detail

        db.expire_all()
        assert admin.is_frozen is False
        assert other_admin.is_frozen is False
    finally:
        _done(db, admin, other_admin)


def test_admin_freeze_user_success_and_idempotent(client: TestClient, db) -> None:
    admin = make_user(db, role="admin")
    user = make_user(db)
    try:
        as_user(admin)
        first = _patch_freeze(client, user.id, action="freeze", reason="测试冻结")
        assert first.status_code == 200, first.text
        assert first.json()["is_frozen"] is True
        assert first.json()["is_active"] is True

        second = _patch_freeze(client, user.id, action="freeze", reason="测试冻结")
        assert second.status_code == 200, second.text
        assert second.json()["is_frozen"] is True
    finally:
        _done(db, admin, user)


def test_freeze_stops_queued_tasks(client: TestClient, db) -> None:
    admin = make_user(db, role="admin")
    user = make_user(db)
    try:
        task = _insert_queued(db, user)
        as_user(admin)
        fr = _patch_freeze(client, user.id, action="freeze", reason="测试冻结")
        assert fr.status_code == 200, fr.text
        db.expire_all()
        db.refresh(task)
        assert task.status == "failed"
        assert task.error_message is not None
        assert QUEUED_STOPPED in task.error_message
    finally:
        _done(db, admin, user)


def test_appeal_unfrozen_400_then_pending_and_duplicate_409(client: TestClient, db) -> None:
    admin = make_user(db, role="admin")
    user = make_user(db)
    try:
        as_user(user)
        before = client.post("/api/appeals", json={"content": "未冻结不能申诉"})
        assert before.status_code == 400

        as_user(admin)
        fr = _patch_freeze(client, user.id, action="freeze", reason="测试冻结")
        assert fr.status_code == 200, fr.text
        db.expire_all()

        as_user(user)
        ok = client.post("/api/appeals", json={"content": "申请解冻测试"})
        assert ok.status_code == 201, ok.text
        assert ok.json()["status"] == "pending"

        dup = client.post("/api/appeals", json={"content": "重复 pending"})
        assert dup.status_code == 409
    finally:
        _done(db, admin, user)


def test_unfreeze_allows_business_without_requeue(client: TestClient, db) -> None:
    admin = make_user(db, role="admin")
    user = make_user(db)
    try:
        task = _insert_queued(db, user)
        as_user(admin)
        fr = _patch_freeze(client, user.id, action="freeze", reason="测试冻结")
        assert fr.status_code == 200, fr.text

        uf = _patch_freeze(client, user.id, action="unfreeze", reason=None)
        assert uf.status_code == 200, uf.text
        assert uf.json()["is_frozen"] is False

        db.expire_all()
        db.refresh(task)
        assert task.status == "failed"
        assert task.error_message is not None
        assert QUEUED_STOPPED in task.error_message

        as_user(user)
        listed = client.get("/api/generations")
        assert listed.status_code == 200, listed.text
        assert "items" in listed.json()
    finally:
        _done(db, admin, user)
