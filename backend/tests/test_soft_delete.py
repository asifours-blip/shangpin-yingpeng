"""软删：终态任务、输出资产闸门、retry_of_id。不调方舟、不碰 MinIO、不碰任务 5。"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.models import GenerationTask, GenerationTaskAsset
from tests.conftest import as_user, cleanup, make_asset, make_user

_FAKE_URL = "http://sdel.test/presign"


def _mock_storage(*, with_get_bytes: bool = False) -> ExitStack:
    """只挡 MinIO 读写，不删对象。闸门仍走真查询。"""
    stack = ExitStack()
    stack.enter_context(
        patch("app.api.assets.storage.presigned_url", return_value=_FAKE_URL)
    )
    if with_get_bytes:
        stack.enter_context(
            patch("app.api.assets.storage.get_bytes", return_value=b"fake-png")
        )
    return stack


def _make_task(db, user, *, status: str = "succeeded", prompt: str = "sdel") -> GenerationTask:
    task = GenerationTask(
        user_id=user.id,
        mode="t2i",
        prompt=prompt,
        params={"size": "2048x2048"},
        status=status,
        review_status="unreviewed",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _link(db, task: GenerationTask, asset, *, role: str, position: int = 0) -> None:
    db.add(
        GenerationTaskAsset(
            task_id=task.id,
            asset_id=asset.id,
            role=role,
            position=position,
        )
    )
    db.commit()


def test_delete_succeeded_idempotent_and_hidden(client: TestClient, db) -> None:
    owner = make_user(db, prefix="sdel")
    keep = _make_task(db, owner, prompt="sdel-keep")
    task = _make_task(db, owner, prompt="sdel-drop")
    task_id = task.id
    keep_id = keep.id
    as_user(owner)
    try:
        r = client.delete(f"/api/generations/{task_id}")
        assert r.status_code == 200, r.text
        assert r.json()["id"] == task_id

        r2 = client.delete(f"/api/generations/{task_id}")
        assert r2.status_code == 200, r2.text

        listed = client.get("/api/generations")
        assert listed.status_code == 200, listed.text
        ids = [item["id"] for item in listed.json()["items"]]
        assert task_id not in ids
        assert keep_id in ids

        detail = client.get(f"/api/generations/{task_id}")
        assert detail.status_code == 404

        review = client.patch(
            f"/api/generations/{task_id}/review",
            json={"review_status": "usable"},
        )
        assert review.status_code == 404
    finally:
        cleanup(db, owner)


def test_delete_queued_or_running_400(client: TestClient, db) -> None:
    owner = make_user(db, prefix="sdel")
    queued = _make_task(db, owner, status="queued", prompt="sdel-queued")
    running = _make_task(db, owner, status="running", prompt="sdel-running")
    as_user(owner)
    try:
        rq = client.delete(f"/api/generations/{queued.id}")
        assert rq.status_code == 400, rq.text
        rr = client.delete(f"/api/generations/{running.id}")
        assert rr.status_code == 400, rr.text
    finally:
        cleanup(db, owner)


def test_delete_others_task_404(client: TestClient, db) -> None:
    owner = make_user(db, prefix="sdel")
    stranger = make_user(db, prefix="sdel")
    task = _make_task(db, owner, prompt="sdel-foreign")
    as_user(stranger)
    try:
        r = client.delete(f"/api/generations/{task.id}")
        assert r.status_code == 404, r.text
    finally:
        cleanup(db, owner, stranger)


def test_exclusive_deleted_output_hidden_from_owner(client: TestClient, db) -> None:
    owner = make_user(db, prefix="sdel")
    admin = make_user(db, role="admin", prefix="sdel")
    asset = make_asset(db, owner)
    task = _make_task(db, owner, prompt="sdel-exclusive-out")
    _link(db, task, asset, role="output")
    as_user(owner)
    try:
        deleted = client.delete(f"/api/generations/{task.id}")
        assert deleted.status_code == 200, deleted.text

        with _mock_storage(with_get_bytes=True):
            owner_url = client.get(f"/api/assets/{asset.id}/url")
            assert owner_url.status_code == 404, owner_url.text
            owner_file = client.get(f"/api/assets/{asset.id}/file")
            assert owner_file.status_code == 404, owner_file.text

            as_user(admin)
            admin_url = client.get(f"/api/assets/{asset.id}/url")
            assert admin_url.status_code == 200, admin_url.text
            admin_file = client.get(f"/api/assets/{asset.id}/file")
            # MinIO 无对象时真接口可能 404；此处 mock get_bytes，过闸门即 200
            assert admin_file.status_code == 200, admin_file.text
    finally:
        cleanup(db, owner, admin)


def test_shared_input_still_visible_after_output_deleted(client: TestClient, db) -> None:
    owner = make_user(db, prefix="sdel")
    asset = make_asset(db, owner)
    alive = _make_task(db, owner, prompt="sdel-alive-product")
    dead = _make_task(db, owner, prompt="sdel-dead-output")
    _link(db, alive, asset, role="product")
    _link(db, dead, asset, role="output")
    as_user(owner)
    try:
        deleted = client.delete(f"/api/generations/{dead.id}")
        assert deleted.status_code == 200, deleted.text

        with _mock_storage():
            r = client.get(f"/api/assets/{asset.id}/url")
            assert r.status_code == 200, r.text
    finally:
        cleanup(db, owner)


def test_retry_of_deleted_task_404(client: TestClient, db) -> None:
    owner = make_user(db, prefix="sdel")
    origin = _make_task(db, owner, prompt="sdel-retry-origin")
    origin_id = origin.id
    as_user(owner)
    try:
        deleted = client.delete(f"/api/generations/{origin_id}")
        assert deleted.status_code == 200, deleted.text
        r = client.post(
            "/api/generations",
            json={
                "prompt": "sdel-retry",
                "mode": "t2i",
                "size": "2048x2048",
                "retry_of_id": origin_id,
            },
        )
        assert r.status_code == 404, r.text
    finally:
        cleanup(db, owner)
