"""三镜头版本、审核和导出的数据库契约。外部模型调用均由任务行模拟。"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager

import pytest
from PIL import Image
from sqlalchemy import delete, select

from app.core.config import settings
from app.models import GenerationTask, GenerationTaskAsset, ImageAsset
from app.models.campaign import CampaignRun, PipelineStep
from app.services import storage
from tests.conftest import as_user, cleanup, make_user
from tests.test_review_versions import _open, _ready_platform


@contextmanager
def _stream(_key):
    yield io.BytesIO(b"stored-bytes")


def _action(client, campaign_id: int, version: int, shot: int, action: str, **extra):
    return client.post(
        f"/api/campaigns/{campaign_id}/storyboard/shots/{shot}/{action}",
        headers={"If-Match": str(version)},
        json={"expected_version": version, **extra},
    )


def _finish_task(db, task_id: int, owner_id: int, mime: str) -> int:
    asset = ImageAsset(
        owner_id=owner_id, bucket=settings.MINIO_BUCKET,
        object_key=f"tests/storyboard/{task_id}.{'mp4' if mime == 'video/mp4' else 'png'}",
        mime=mime, size_bytes=len(b"stored-bytes"), width=720, height=480,
    )
    db.add(asset)
    db.flush()
    db.add(GenerationTaskAsset(task_id=task_id, asset_id=asset.id, role="output", position=0))
    task = db.get(GenerationTask, task_id)
    task.status = "succeeded"
    db.commit()
    return asset.id


def _started(client, db):
    user, product, _fact, campaign_id, detail = _open(client, db)
    _ready_platform(db, detail, "douyin")
    brief = db.scalar(select(PipelineStep).where(
        PipelineStep.run_id == detail["run"]["id"], PipelineStep.step_key == "brief",
    ))
    brief.status = "succeeded"
    db.commit()
    started = client.post(
        f"/api/campaigns/{campaign_id}/storyboard/start",
        headers={"If-Match": "1"}, json={"expected_version": 1},
    )
    assert started.status_code == 200, started.text
    return user, product, campaign_id, started.json()["version"]


def test_start_requires_all_three_pipeline_steps(client, db):
    user, _product, _fact, campaign_id, detail = _open(client, db)
    try:
        _ready_platform(db, detail, "douyin")
        run_id = detail["run"]["id"]
        brief = db.scalar(select(PipelineStep).where(
            PipelineStep.run_id == run_id, PipelineStep.step_key == "brief",
        ))
        brief.status = "succeeded"
        db.execute(delete(PipelineStep).where(
            PipelineStep.run_id == run_id, PipelineStep.step_key == "video:douyin",
        ))
        db.commit()
        result = client.post(
            f"/api/campaigns/{campaign_id}/storyboard/start",
            headers={"If-Match": "1"}, json={"expected_version": 1},
        )
        assert result.status_code == 422
        assert result.json()["detail"]["code"] == "pipeline_incomplete"
    finally:
        cleanup(db, user)


def test_storyboard_three_shots_review_lock_redo_and_export(client, db, monkeypatch):
    monkeypatch.setattr(settings, "ARK_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ARK_IMAGE_ENDPOINT", "test-image")
    monkeypatch.setattr(settings, "ARK_VIDEO_ENDPOINT", "test-video")
    monkeypatch.setattr(storage, "object_exists", lambda _key: True)
    monkeypatch.setattr(storage, "get_bytes", lambda _key: b"stored-bytes")
    monkeypatch.setattr(storage, "open_stream", _stream)
    user, product, campaign_id, version = _started(client, db)
    try:
        early = _action(client, campaign_id, version, 0, "submit_video")
        assert early.status_code == 422
        assert early.json()["detail"]["code"] == "frame_unreviewed"
        frame_assets = []
        video_assets = []
        for shot in range(3):
            submitted = _action(client, campaign_id, version, shot, "submit_frame")
            assert submitted.status_code == 200, submitted.text
            version = submitted.json()["version"]
            side = client.get(f"/api/campaigns/{campaign_id}/review").json()["platforms"][0]
            task_id = side["current"]["storyboard"]["shots"][shot]["first_frame_task_id"]
            task = db.get(GenerationTask, task_id)
            input_link = db.scalar(select(GenerationTaskAsset).where(
                GenerationTaskAsset.task_id == task_id, GenerationTaskAsset.role == "product",
            ))
            assert task.mode == "i2i" and input_link.asset_id == product.primary_asset_id
            frame_assets.append(_finish_task(db, task_id, user.id, "image/png"))
            approved = _action(client, campaign_id, version, shot, "review_frame", accepted=True)
            assert approved.status_code == 200, approved.text
            version = approved.json()["version"]
            submitted_video = _action(client, campaign_id, version, shot, "submit_video")
            assert submitted_video.status_code == 200, submitted_video.text
            version = submitted_video.json()["version"]
            current = client.get(f"/api/campaigns/{campaign_id}/review").json()["platforms"][0]["current"]
            video_id = current["storyboard"]["shots"][shot]["video_task_id"]
            video_input = db.scalar(select(GenerationTaskAsset).where(
                GenerationTaskAsset.task_id == video_id, GenerationTaskAsset.role == "product",
            ))
            assert video_input.asset_id == frame_assets[shot]
            video_assets.append(_finish_task(db, video_id, user.id, "video/mp4"))
            accepted = _action(client, campaign_id, version, shot, "review_video", accepted=True)
            assert accepted.status_code == 200, accepted.text
            version = accepted.json()["version"]
            locked = _action(client, campaign_id, version, shot, "lock")
            assert locked.status_code == 200, locked.text
            version = locked.json()["version"]
        current = client.get(f"/api/campaigns/{campaign_id}/review").json()["platforms"][0]["current"]
        assert [item["asset_id"] for item in current["assets"]] == [
            value for pair in zip(frame_assets, video_assets) for value in pair
        ]
        approved = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/approve",
            headers={"If-Match": str(version)}, json={"expected_version": version},
        )
        assert approved.status_code == 200, approved.text
        delivered = client.get(f"/api/campaigns/{campaign_id}/variants/douyin/export?version={version}")
        assert delivered.status_code == 200, delivered.text
        with zipfile.ZipFile(io.BytesIO(delivered.content)) as bundle:
            names = bundle.namelist()
            assert "shots/01-first-frame.png" in names
            assert "shots/02-video.mp4" in names
            assert "shots/03-video.mp4" in names
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["media_scope"] == "three_reviewed_shots_not_composited"
            assert len([item for item in manifest["files"] if item["role"].startswith("shot_")]) == 6
        blocked = _action(client, campaign_id, version, 1, "redo", stage="first_frame")
        assert blocked.status_code == 422
        assert blocked.json()["detail"]["code"] == "shot_locked"
        unlocked = _action(client, campaign_id, version, 1, "unlock")
        assert unlocked.status_code == 200, unlocked.text
        version = unlocked.json()["version"]
        stale_export = client.head(f"/api/campaigns/{campaign_id}/variants/douyin/export?version={version - 1}")
        assert stale_export.status_code == 409
        redone = _action(client, campaign_id, version, 1, "redo", stage="first_frame", prompt="帆布托特，侧面材质细节")
        assert redone.status_code == 200, redone.text
        version = redone.json()["version"]
        after = client.get(f"/api/campaigns/{campaign_id}/review").json()["platforms"][0]["current"]["storyboard"]["shots"]
        assert after[0]["first_frame_asset_id"] == frame_assets[0]
        assert after[0]["video_asset_id"] == video_assets[0]
        assert after[2]["first_frame_asset_id"] == frame_assets[2]
        assert after[2]["video_asset_id"] == video_assets[2]
        assert after[1]["first_frame_asset_id"] is None and after[1]["video_asset_id"] is None
        assert client.head(f"/api/campaigns/{campaign_id}/variants/douyin/export?version={version}").status_code == 422
    finally:
        cleanup(db, user)


def test_wrong_owner_and_stale_version_cannot_submit(client, db, monkeypatch):
    monkeypatch.setattr(settings, "ARK_API_KEY", "test-key")
    monkeypatch.setattr(settings, "ARK_IMAGE_ENDPOINT", "test-image")
    user, _product, campaign_id, version = _started(client, db)
    other = make_user(db)
    try:
        as_user(other)
        assert _action(client, campaign_id, version, 0, "submit_frame").status_code == 404
        as_user(user)
        first = _action(client, campaign_id, version, 0, "submit_frame")
        assert first.status_code == 200
        stale = _action(client, campaign_id, version, 1, "submit_frame")
        assert stale.status_code == 409
        run = db.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign_id))
        assert run.budget_reserved <= run.generation_budget
    finally:
        cleanup(db, user, other)


@pytest.mark.real_storage
def test_storyboard_worker_uses_real_minio_and_blocks_uncomposited_publish(client, db, monkeypatch):
    from app.services import worker_loop

    source_image = io.BytesIO()
    Image.new("RGB", (24, 24), "#cebda3").save(source_image, format="PNG")
    frame_bytes = source_image.getvalue()
    video_bytes = (Path(__file__).parent / "fixtures" / "publish-one-second.mp4").read_bytes()
    monkeypatch.setattr(settings, "ARK_API_KEY", "synthetic-no-remote")
    monkeypatch.setattr(settings, "ARK_IMAGE_ENDPOINT", "synthetic-image")
    monkeypatch.setattr(settings, "ARK_VIDEO_ENDPOINT", "synthetic-video")
    image_inputs = []
    video_inputs = []

    def fake_image(prompt, size, images):
        assert size == "2048x2048" and len(images) == 1
        image_inputs.append(images[0])
        return {"data": [{"url": "mock://frame", "size": "24x24"}]}

    def fake_video(prompt, image, **params):
        assert params["duration"] == 5
        video_inputs.append(image)
        return "mock-video-task"

    monkeypatch.setattr(worker_loop, "generate_i2i", fake_image)
    monkeypatch.setattr(worker_loop, "create_i2v_task", fake_video)
    monkeypatch.setattr(worker_loop, "wait_i2v_result", lambda _id, on_tick: "mock://video")
    monkeypatch.setattr(worker_loop, "download_image", lambda url: (
        (frame_bytes, "image/png") if url == "mock://frame" else (video_bytes, "video/mp4")
    ))
    user, product, campaign_id, version = _started(client, db)
    source_key = db.get(ImageAsset, product.primary_asset_id).object_key
    object_keys = [source_key]
    try:
        storage.put_bytes(source_key, frame_bytes, "image/png")
        shot_assets = []
        for shot in range(3):
            submitted = _action(client, campaign_id, version, shot, "submit_frame")
            assert submitted.status_code == 200, submitted.text
            version = submitted.json()["version"]
            board = client.get(f"/api/campaigns/{campaign_id}/review").json()["platforms"][0]["current"]["storyboard"]
            frame_task_id = board["shots"][shot]["first_frame_task_id"]
            queued_redo = _action(client, campaign_id, version, shot, "redo", stage="first_frame")
            assert queued_redo.status_code == 422
            assert queued_redo.json()["detail"]["code"] == "result_unknown"
            assert worker_loop.claim_one(db) == frame_task_id
            worker_loop.process_task(db, frame_task_id)
            db.expire_all()
            assert db.get(GenerationTask, frame_task_id).status == "succeeded"
            frame_link = db.scalar(select(GenerationTaskAsset).where(
                GenerationTaskAsset.task_id == frame_task_id, GenerationTaskAsset.role == "output",
            ))
            frame_asset = db.get(ImageAsset, frame_link.asset_id)
            object_keys.append(frame_asset.object_key)
            assert storage.get_bytes(frame_asset.object_key) == frame_bytes
            reviewed = _action(client, campaign_id, version, shot, "review_frame", accepted=True)
            assert reviewed.status_code == 200, reviewed.text
            version = reviewed.json()["version"]

            submitted = _action(client, campaign_id, version, shot, "submit_video")
            assert submitted.status_code == 200, submitted.text
            version = submitted.json()["version"]
            board = client.get(f"/api/campaigns/{campaign_id}/review").json()["platforms"][0]["current"]["storyboard"]
            video_task_id = board["shots"][shot]["video_task_id"]
            queued_redo = _action(client, campaign_id, version, shot, "redo", stage="first_frame")
            assert queued_redo.status_code == 422
            assert queued_redo.json()["detail"]["code"] == "result_unknown"
            assert worker_loop.claim_one(db) == video_task_id
            worker_loop.process_task(db, video_task_id)
            db.expire_all()
            assert db.get(GenerationTask, video_task_id).status == "succeeded"
            video_link = db.scalar(select(GenerationTaskAsset).where(
                GenerationTaskAsset.task_id == video_task_id, GenerationTaskAsset.role == "output",
            ))
            video_asset = db.get(ImageAsset, video_link.asset_id)
            object_keys.append(video_asset.object_key)
            assert storage.get_bytes(video_asset.object_key) == video_bytes
            reviewed = _action(client, campaign_id, version, shot, "review_video", accepted=True)
            assert reviewed.status_code == 200, reviewed.text
            version = reviewed.json()["version"]
            locked = _action(client, campaign_id, version, shot, "lock")
            assert locked.status_code == 200, locked.text
            version = locked.json()["version"]
            shot_assets.append((frame_asset.id, video_asset.id))
        assert len(image_inputs) == len(video_inputs) == 3
        assert all(value.startswith("data:image/png;base64,") for value in image_inputs + video_inputs)
        approved = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/approve",
            headers={"If-Match": str(version)}, json={"expected_version": version},
        )
        assert approved.status_code == 200, approved.text
        desk = client.get(f"/api/campaigns/{campaign_id}/publish").json()["platforms"][0]
        assert any(item["code"] == "storyboard_not_composited" for item in desk["content_blockers"])
        scheduled = client.post(f"/api/campaigns/{campaign_id}/publish", json={
            "platform": "douyin", "expected_version": version,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        })
        assert scheduled.status_code == 422
        assert scheduled.json()["detail"]["code"] == "storyboard_not_composited"
        delivered = client.get(f"/api/campaigns/{campaign_id}/variants/douyin/export?version={version}")
        assert delivered.status_code == 200, delivered.text
        with zipfile.ZipFile(io.BytesIO(delivered.content)) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            media = [item for item in manifest["files"] if item["role"].startswith("shot_")]
            assert len(media) == 6 and "final_video" not in bundle.namelist()
            assert [item["asset_id"] for item in media] == [
                asset_id for pair in shot_assets for asset_id in pair
            ]
            for item in manifest["files"]:
                payload = bundle.read(item["file"])
                assert item["byte_size"] == len(payload)
                assert item["sha256"] == hashlib.sha256(payload).hexdigest()
        locked_redo = _action(client, campaign_id, version, 1, "redo", stage="video")
        assert locked_redo.status_code == 422
        unlocked = _action(client, campaign_id, version, 1, "unlock")
        assert unlocked.status_code == 200, unlocked.text
        version = unlocked.json()["version"]
        redone = _action(client, campaign_id, version, 1, "redo", stage="video")
        assert redone.status_code == 200, redone.text
        after = client.get(f"/api/campaigns/{campaign_id}/review").json()["platforms"][0]["current"]["storyboard"]["shots"]
        assert after[0]["first_frame_asset_id"] == shot_assets[0][0]
        assert after[0]["video_asset_id"] == shot_assets[0][1]
        assert after[1]["first_frame_asset_id"] == shot_assets[1][0]
        assert after[1]["video_asset_id"] is None
        assert after[2]["first_frame_asset_id"] == shot_assets[2][0]
        assert after[2]["video_asset_id"] == shot_assets[2][1]
    finally:
        cleanup(db, user)
        for key in object_keys:
            storage.delete_object(key)
