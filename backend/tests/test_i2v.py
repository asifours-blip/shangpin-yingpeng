"""图生视频：创建入队 + Worker 替身。禁止真调方舟。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app.models import GenerationTask, GenerationTaskAsset, ImageAsset
from app.services.ark import extract_video_url
from tests.conftest import as_user, cleanup, make_asset, make_user


def test_extract_video_url_shapes():
    assert extract_video_url({"content": {"video_url": "https://tos.example/a.mp4"}}) == "https://tos.example/a.mp4"
    assert extract_video_url({"output": {"video_url": "https://tos.example/b.mp4"}}) == "https://tos.example/b.mp4"
    assert (
        extract_video_url({"content": [{"video_url": "https://tos.example/c.mp4"}]})
        == "https://tos.example/c.mp4"
    )


def test_create_i2v_queues_with_product(client, db):
    user = make_user(db)
    asset = make_asset(db, user)
    as_user(user)
    try:
        resp = client.post(
            "/api/generations",
            json={
                "prompt": "商品缓慢旋转展示",
                "mode": "i2v",
                "size": "2048x2048",
                "product_asset_id": asset.id,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "i2v"
        assert body["status"] == "queued"
        assert body["params"]["duration"] == 5
        assert body["params"]["resolution"] == "480p"
        roles = {item["role"] for item in body["assets"]}
        assert roles == {"product"}
    finally:
        cleanup(db, user)


def test_create_i2v_rejects_scene(client, db):
    user = make_user(db)
    product = make_asset(db, user)
    scene = make_asset(db, user)
    as_user(user)
    try:
        resp = client.post(
            "/api/generations",
            json={
                "prompt": "商品缓慢旋转展示",
                "mode": "i2v",
                "product_asset_id": product.id,
                "scene_asset_id": scene.id,
            },
        )
        assert resp.status_code == 400, resp.text
    finally:
        cleanup(db, user)


def test_create_i2v_requires_image(client, db):
    user = make_user(db)
    as_user(user)
    try:
        resp = client.post(
            "/api/generations",
            json={"prompt": "商品缓慢旋转展示", "mode": "i2v"},
        )
        assert resp.status_code == 400, resp.text
    finally:
        cleanup(db, user)


def test_worker_i2v_persists_mp4(client, db):
    from app.services import worker_loop as wl

    user = make_user(db)
    asset = make_asset(db, user)
    as_user(user)
    try:
        created = client.post(
            "/api/generations",
            json={
                "prompt": "商品缓慢旋转展示",
                "mode": "i2v",
                "product_asset_id": asset.id,
            },
        )
        assert created.status_code == 200, created.text
        task_id = created.json()["id"]
        row = db.get(GenerationTask, task_id)
        assert row is not None
        row.status = "running"
        db.commit()

        with (
            patch.object(wl, "create_i2v_task", return_value="cgt-demo"),
            patch.object(wl, "wait_i2v_result", return_value="https://tos.example/out.mp4"),
            patch.object(wl, "download_image", return_value=(b"fake-mp4", "video/mp4")),
            patch.object(wl, "_read_object_bytes", return_value=b"\x89PNG"),
            patch.object(wl, "bytes_to_data_url", return_value="data:image/png;base64,xx"),
            patch.object(wl.storage, "put_bytes"),
            patch.object(wl.storage, "new_object_key", return_value=f"generations/{task_id}/demo.mp4"),
            patch.object(wl, "_status_is_running", return_value=True),
            patch.object(wl, "touch_worker_heartbeat"),
        ):
            wl.process_task(db, task_id)

        db.expire_all()
        task = db.get(GenerationTask, task_id)
        assert task is not None
        assert task.status == "succeeded"
        link = db.scalar(
            select(GenerationTaskAsset).where(
                GenerationTaskAsset.task_id == task_id,
                GenerationTaskAsset.role == "output",
            )
        )
        assert link is not None
        out = db.get(ImageAsset, link.asset_id)
        assert out is not None
        assert out.mime == "video/mp4"
    finally:
        cleanup(db, user)


def test_run_i2v_uses_product_asset():
    from app.services import worker_loop as wl

    asset = SimpleNamespace(object_key="k-product", mime="image/png")
    link = SimpleNamespace(role="product", position=0, id=1, asset=asset)
    db = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = [link]
    db.scalars.return_value = scalars
    task = SimpleNamespace(id=88, prompt="转一圈", params={"duration": 5, "resolution": "480p", "ratio": "16:9"})

    with (
        patch.object(wl, "_read_object_bytes", return_value=b"img"),
        patch.object(wl, "bytes_to_data_url", return_value="data:image/png;base64,xx"),
        patch.object(wl, "create_i2v_task", return_value="cgt-1") as create,
        patch.object(wl, "wait_i2v_result", return_value="https://tos.example/v.mp4") as wait,
        patch.object(wl, "download_image", return_value=(b"mp4", "video/mp4")),
        patch.object(wl, "touch_worker_heartbeat"),
    ):
        raw, mime = wl._run_i2v(db, task)

    assert raw == b"mp4"
    assert mime == "video/mp4"
    assert create.call_args[0][0] == "转一圈"
    assert wait.call_args[0][0] == "cgt-1"
