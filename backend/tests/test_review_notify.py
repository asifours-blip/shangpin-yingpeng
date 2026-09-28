"""管理员打回通知：普通用户不落行、幂等、handle 不改审核。

隔离 ops_ 用户，不碰 demo/admin/任务 5，不调方舟。
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import GenerationTask, GenerationTaskAsset, Notification
from tests.conftest import as_user, cleanup, make_asset, make_user


def _succeeded_task(db, owner, *, prompt: str = "ops review notify"):
    """make_asset + 直接插入 succeeded 任务，不走生成接口、不调方舟。"""
    asset = make_asset(db, owner)
    task = GenerationTask(
        user_id=owner.id,
        mode="t2i",
        prompt=prompt,
        params={"size": "2048x2048"},
        status="succeeded",
        review_status="unreviewed",
    )
    db.add(task)
    db.flush()
    db.add(
        GenerationTaskAsset(
            task_id=task.id,
            asset_id=asset.id,
            role="output",
            position=0,
        )
    )
    db.commit()
    db.refresh(task)
    return task


def _pending_items(client):
    r = client.get("/api/notifications")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _notification_rows(db, user_id: int, task_id: int | None = None):
    db.expire_all()
    stmt = select(Notification).where(Notification.user_id == user_id)
    if task_id is not None:
        stmt = stmt.where(Notification.task_id == task_id)
    return list(db.scalars(stmt).all())


def test_user_needs_revision_creates_no_notification_row(client, db):
    owner = make_user(db)
    task = _succeeded_task(db, owner)
    as_user(owner)
    try:
        r = client.patch(
            f"/api/generations/{task.id}/review",
            json={"review_status": "needs_revision", "review_reason": "自己觉得构图不行"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["review_status"] == "needs_revision"
        assert _notification_rows(db, owner.id, task.id) == []
        assert _pending_items(client) == []
    finally:
        cleanup(db, owner)


def test_admin_reject_requires_reason_then_owner_gets_one_pending(client, db):
    owner = make_user(db)
    admin = make_user(db, role="admin")
    task = _succeeded_task(db, owner)
    try:
        as_user(admin)
        missing = client.patch(
            f"/api/admin/generations/{task.id}/review",
            json={"review_status": "needs_revision"},
        )
        assert missing.status_code == 400, missing.text

        blank = client.patch(
            f"/api/admin/generations/{task.id}/review",
            json={"review_status": "needs_revision", "review_reason": "  "},
        )
        assert blank.status_code == 400, blank.text
        assert _notification_rows(db, owner.id, task.id) == []

        ok = client.patch(
            f"/api/admin/generations/{task.id}/review",
            json={"review_status": "needs_revision", "review_reason": "颜色偏差"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["review_status"] == "needs_revision"

        as_user(owner)
        items = _pending_items(client)
        assert len(items) == 1
        assert items[0]["task_id"] == task.id
        assert items[0]["status"] == "pending"
        assert items[0]["reason"] == "颜色偏差"
    finally:
        cleanup(db, owner, admin)


def test_same_admin_same_reject_does_not_duplicate_or_resurrect(client, db):
    owner = make_user(db)
    admin = make_user(db, role="admin")
    task = _succeeded_task(db, owner)
    payload = {"review_status": "needs_revision", "review_reason": "颜色偏差"}
    try:
        as_user(admin)
        first = client.patch(f"/api/admin/generations/{task.id}/review", json=payload)
        assert first.status_code == 200, first.text

        as_user(owner)
        items = _pending_items(client)
        assert len(items) == 1
        nid = items[0]["id"]

        as_user(admin)
        again = client.patch(f"/api/admin/generations/{task.id}/review", json=payload)
        assert again.status_code == 200, again.text

        as_user(owner)
        still = _pending_items(client)
        assert len(still) == 1
        assert still[0]["id"] == nid
        assert still[0]["status"] == "pending"

        handled = client.patch(f"/api/notifications/{nid}/handle")
        assert handled.status_code == 200, handled.text
        assert handled.json()["status"] == "handled"
        assert _pending_items(client) == []

        as_user(admin)
        resurrect = client.patch(f"/api/admin/generations/{task.id}/review", json=payload)
        assert resurrect.status_code == 200, resurrect.text

        as_user(owner)
        assert _pending_items(client) == []
        rows = _notification_rows(db, owner.id, task.id)
        assert len(rows) == 1
        assert rows[0].status == "handled"
    finally:
        cleanup(db, owner, admin)


def test_user_usable_then_admin_first_reject_notifies(client, db):
    owner = make_user(db)
    admin = make_user(db, role="admin")
    task = _succeeded_task(db, owner)
    try:
        as_user(owner)
        self_ok = client.patch(
            f"/api/generations/{task.id}/review",
            json={"review_status": "usable", "review_reason": "结构可接受"},
        )
        assert self_ok.status_code == 200, self_ok.text
        assert self_ok.json()["review_status"] == "usable"
        assert _pending_items(client) == []
        assert _notification_rows(db, owner.id, task.id) == []

        as_user(admin)
        reject = client.patch(
            f"/api/admin/generations/{task.id}/review",
            json={"review_status": "needs_revision", "review_reason": "灯光过曝"},
        )
        assert reject.status_code == 200, reject.text

        as_user(owner)
        items = _pending_items(client)
        assert len(items) == 1
        assert items[0]["task_id"] == task.id
        assert items[0]["status"] == "pending"
        assert items[0]["reason"] == "灯光过曝"
    finally:
        cleanup(db, owner, admin)


def test_admin_usable_closes_pending_then_reject_creates_new(client, db):
    owner = make_user(db)
    admin = make_user(db, role="admin")
    task = _succeeded_task(db, owner)
    try:
        as_user(admin)
        reject = client.patch(
            f"/api/admin/generations/{task.id}/review",
            json={"review_status": "needs_revision", "review_reason": "颜色偏差"},
        )
        assert reject.status_code == 200, reject.text

        as_user(owner)
        first = _pending_items(client)
        assert len(first) == 1
        first_id = first[0]["id"]

        as_user(admin)
        usable = client.patch(
            f"/api/admin/generations/{task.id}/review",
            json={"review_status": "usable", "review_reason": "已修好"},
        )
        assert usable.status_code == 200, usable.text
        assert usable.json()["review_status"] == "usable"

        as_user(owner)
        assert _pending_items(client) == []

        as_user(admin)
        again = client.patch(
            f"/api/admin/generations/{task.id}/review",
            json={"review_status": "needs_revision", "review_reason": "二次打回"},
        )
        assert again.status_code == 200, again.text

        as_user(owner)
        items = _pending_items(client)
        assert len(items) == 1
        assert items[0]["status"] == "pending"
        assert items[0]["reason"] == "二次打回"
        assert items[0]["id"] != first_id
    finally:
        cleanup(db, owner, admin)


def test_handle_keeps_review_status_idempotent_and_foreign_404(client, db):
    owner = make_user(db)
    admin = make_user(db, role="admin")
    stranger = make_user(db)
    task = _succeeded_task(db, owner)
    try:
        as_user(admin)
        reject = client.patch(
            f"/api/admin/generations/{task.id}/review",
            json={"review_status": "needs_revision", "review_reason": "颜色偏差"},
        )
        assert reject.status_code == 200, reject.text

        as_user(owner)
        items = _pending_items(client)
        assert len(items) == 1
        nid = items[0]["id"]

        as_user(stranger)
        foreign = client.patch(f"/api/notifications/{nid}/handle")
        assert foreign.status_code == 404, foreign.text

        as_user(owner)
        first = client.patch(f"/api/notifications/{nid}/handle")
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "handled"

        gen = client.get(f"/api/generations/{task.id}")
        assert gen.status_code == 200, gen.text
        assert gen.json()["review_status"] == "needs_revision"
        assert gen.json()["review_reason"] == "颜色偏差"

        second = client.patch(f"/api/notifications/{nid}/handle")
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "handled"

        gen2 = client.get(f"/api/generations/{task.id}")
        assert gen2.status_code == 200, gen2.text
        assert gen2.json()["review_status"] == "needs_revision"
    finally:
        cleanup(db, owner, admin, stranger)
