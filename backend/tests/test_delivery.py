"""Approved-version delivery must use the review snapshot and readable media."""

import io
import hashlib
import json
import asyncio
import os
import threading
import time
import zipfile
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import ImageAsset
from app.models.campaign import ContentVariant, VariantAsset, VariantReview
from app.services import storage
from app.services import delivery
from app.services.variant_review import edit_variant
from tests.conftest import as_user, cleanup, make_user
from tests.test_review_versions import _open, _ready_platform


def _approved(client, db, monkeypatch, platform="douyin"):
    user, _product, _fact, campaign_id, detail = _open(client, db)
    try:
        _ready_platform(db, detail, platform)
        db.flush()  # SessionLocal has autoflush=False; include pending VariantAsset links.
        variant = db.scalar(select(ContentVariant).where(
            ContentVariant.campaign_id == campaign_id,
            ContentVariant.platform == platform,
        ))
        for link in db.scalars(select(VariantAsset).where(VariantAsset.variant_id == variant.id)):
            db.get(ImageAsset, link.asset_id).bucket = settings.MINIO_BUCKET
            if platform == "douyin" and link.role == "final_video":
                link.position = 0  # The real worker numbers this role independently from cover.
        db.commit()
        monkeypatch.setattr(storage, "object_exists", lambda _key: True)
        monkeypatch.setattr(storage, "get_bytes", lambda _key: b"stored-bytes")

        @contextmanager
        def media_stream(_key):
            yield io.BytesIO(b"stored-bytes")

        monkeypatch.setattr(storage, "open_stream", media_stream, raising=False)
        approved = client.post(
            f"/api/campaigns/{campaign_id}/variants/{platform}/approve",
            json={"expected_version": 1},
        )
        assert approved.status_code == 200, approved.text
        db.expire_all()
        review = db.get(VariantReview, approved.json()["id"])
        for entry in review.asset_order:
            asset = db.get(ImageAsset, entry["asset_id"])
            assert asset is not None
            assert asset.owner_id == user.id
            assert asset.bucket == settings.MINIO_BUCKET
        return user, campaign_id
    except Exception:
        db.rollback()
        cleanup(db, user)
        raise


def test_export_approved_snapshot_and_preflight(client, db, monkeypatch, tmp_path):
    user, campaign_id = _approved(client, db, monkeypatch)
    monkeypatch.setattr(delivery, "_TEMP_DIR", tmp_path)
    try:
        url = f"/api/campaigns/{campaign_id}/variants/douyin/export?version=1"
        original_stream = storage.open_stream
        def no_media_read(_key):
            raise AssertionError("HEAD must not open media")
        monkeypatch.setattr(storage, "open_stream", no_media_read)
        head = client.head(url)
        assert head.status_code == 200
        assert head.content == b""
        monkeypatch.setattr(storage, "open_stream", original_stream)
        response = client.get(url)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert "attachment;" in response.headers["content-disposition"]
        assert response.headers["cache-control"] == "private, no-store"
        assert list(tmp_path.glob("ops-export-*.zip")) == []
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            names = bundle.namelist()
            assert names == ["01-cover.png", "02-video.mp4", "title.txt", "body.txt", "hashtags.txt", "manifest.json"]
            assert bundle.read("01-cover.png") == b"stored-bytes"
            assert bundle.read("02-video.mp4") == b"stored-bytes"
            assert bundle.read("title.txt").decode("utf-8") == "帆布托特"
            assert bundle.read("body.txt").decode("utf-8") == "材质是帆布。"
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["campaign_id"] == campaign_id
            assert manifest["platform"] == "douyin"
            assert manifest["version"] == 1
            assert manifest["reviewed_at"]
            assert [item["file"] for item in manifest["files"]] == names[:-1]
            assert [item["role"] for item in manifest["files"]] == ["cover", "final_video", "copy", "copy", "copy"]
            assert [item["position"] for item in manifest["files"][:2]] == [0, 0]
            assert [item["export_order"] for item in manifest["files"]] == list(range(5))
            assert all(item["sha256"] == hashlib.sha256(bundle.read(item["file"])).hexdigest() for item in manifest["files"])
    finally:
        cleanup(db, user)


def test_export_rejects_other_account_and_invalidated_approval(client, db, monkeypatch):
    user, campaign_id = _approved(client, db, monkeypatch)
    other = make_user(db)
    admin = make_user(db, role="admin")
    try:
        url = f"/api/campaigns/{campaign_id}/variants/douyin/export?version=1"
        as_user(other)
        assert client.head(url).status_code == 404
        assert client.get(url).status_code == 404
        as_user(admin)
        assert client.get(url).status_code == 404
        as_user(user)
        changed = client.patch(
            f"/api/campaigns/{campaign_id}/variants/douyin",
            json={"expected_version": 1, "title": "新版标题"},
        )
        assert changed.status_code == 200, changed.text
        stale = client.head(url)
        assert stale.status_code == 409
        assert stale.headers["x-export-error-code"] == "version_changed"
        assert client.get(url).status_code == 409
    finally:
        cleanup(db, user, other, admin)


def test_export_refuses_missing_media_and_unapproved_content(client, db, monkeypatch):
    user, campaign_id = _approved(client, db, monkeypatch, "xiaohongshu")
    try:
        url = f"/api/campaigns/{campaign_id}/variants/xiaohongshu/export?version=1"
        @contextmanager
        def missing_stream(_key):
            raise OSError("missing")
            yield

        monkeypatch.setattr(storage, "open_stream", missing_stream)
        result = client.get(url)
        assert result.status_code == 422
        assert result.json()["detail"]["code"] == "asset_unreadable"
        review = db.scalar(select(VariantReview).join(ContentVariant).where(ContentVariant.campaign_id == campaign_id))
        review.decision = "rejected"
        db.commit()
        not_approved = client.get(url)
        assert not_approved.status_code == 422
        assert not_approved.json()["detail"]["code"] in {"not_approved", "review_mismatch"}
    finally:
        cleanup(db, user)


def test_xiaohongshu_delivery_keeps_card_order_and_copy_clean(client, db, monkeypatch):
    user, campaign_id = _approved(client, db, monkeypatch, "xiaohongshu")
    try:
        links = list(db.scalars(
            select(VariantAsset).join(ContentVariant).where(ContentVariant.campaign_id == campaign_id)
            .order_by(VariantAsset.position)
        ))
        payloads = {
            db.get(ImageAsset, links[0].asset_id).object_key: b"cover0123456",
            db.get(ImageAsset, links[1].asset_id).object_key: b"card-0123456",
        }

        @contextmanager
        def distinct_stream(key):
            yield io.BytesIO(payloads[key])

        monkeypatch.setattr(storage, "open_stream", distinct_stream)
        response = client.get(f"/api/campaigns/{campaign_id}/variants/xiaohongshu/export?version=1")
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            names = bundle.namelist()
            assert names[:2] == ["01-cover.png", "02-card.png"]
            assert [bundle.read(name) for name in names[:2]] == [b"cover0123456", b"card-0123456"]
            assert "防水未确认" not in bundle.read("body.txt").decode("utf-8")
            manifest = json.loads(bundle.read("manifest.json"))
            assert [item["file"] for item in manifest["files"]] == names[:-1]
            assert [item["sha256"] for item in manifest["files"]] == [
                hashlib.sha256(bundle.read(name)).hexdigest() for name in names[:-1]
            ]
    finally:
        cleanup(db, user)


def test_edit_during_media_read_exports_only_the_frozen_approved_version(client, db, monkeypatch, tmp_path):
    user, campaign_id = _approved(client, db, monkeypatch)
    monkeypatch.setattr(delivery, "_TEMP_DIR", tmp_path)
    started = threading.Event()
    release = threading.Event()
    results = []

    @contextmanager
    def delayed_stream(_key):
        if not started.is_set():
            started.set()
            assert release.wait(10)
        yield io.BytesIO(b"newer-bytes!" if "/new-cover-" in _key else b"stored-bytes")

    monkeypatch.setattr(storage, "open_stream", delayed_stream)

    def export_in_other_session():
        with SessionLocal() as session:
            snapshot = delivery.freeze_delivery(session, user, campaign_id, "douyin", 1)
            path = delivery.build_archive(snapshot)
            results.append((snapshot, path.read_bytes()))
            path.unlink()

    thread = threading.Thread(target=export_in_other_session)
    try:
        thread.start()
        assert started.wait(10)
        with SessionLocal() as editor:
            changed = edit_variant(
                editor, user, campaign_id, "douyin",
                expected_version=1, title="这是后来的新版", body=None, hashtags=None,
            )
            assert changed.version == 2
            newer = ImageAsset(
                owner_id=user.id, bucket=settings.MINIO_BUCKET,
                object_key=f"tests/delivery/new-cover-{campaign_id}.png",
                mime="image/png", size_bytes=12,
            )
            editor.add(newer)
            editor.flush()
            editor.add(VariantAsset(variant_id=changed.id, asset_id=newer.id, role="cover", position=0))
            editor.commit()
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert len(results) == 1
        snapshot, archive = results[0]
        assert snapshot.version == 1
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            assert bundle.read("title.txt").decode("utf-8") == "帆布托特"
            assert "后来的新版" not in bundle.read("title.txt").decode("utf-8")
            assert bundle.read("01-cover.png") == b"stored-bytes"
            assert b"newer-bytes!" not in archive
            assert json.loads(bundle.read("manifest.json"))["review_id"] == snapshot.review_id
    finally:
        release.set()
        thread.join(10)
        cleanup(db, user)


def test_archive_write_failure_removes_temporary_file(client, db, monkeypatch, tmp_path):
    user, campaign_id = _approved(client, db, monkeypatch)
    monkeypatch.setattr(delivery, "_TEMP_DIR", tmp_path)
    try:
        snapshot = delivery.freeze_delivery(db, user, campaign_id, "douyin", 1)
        original = zipfile.ZipFile.writestr

        def fail_on_text(bundle, name, value, *args, **kwargs):
            if name == "title.txt":
                raise OSError("disk full")
            return original(bundle, name, value, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "writestr", fail_on_text)
        with pytest.raises(OSError, match="disk full"):
            delivery.build_archive(snapshot)
        assert list(tmp_path.glob("ops-export-*.zip")) == []
    finally:
        cleanup(db, user)


def test_response_send_failure_and_stale_cleanup_remove_only_owned_files(tmp_path, monkeypatch):
    archive = tmp_path / "ops-export-send.zip"
    archive.write_bytes(b"delivery")
    response = delivery.CleanupFileResponse(archive, media_type="application/zip")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body":
            raise RuntimeError("client disconnected")

    with pytest.raises(RuntimeError, match="client disconnected"):
        asyncio.run(response({"type": "http", "method": "GET", "headers": []}, receive, send))
    assert not archive.exists()

    monkeypatch.setattr(delivery, "_TEMP_DIR", tmp_path)
    stale = tmp_path / "ops-export-old.zip"
    unrelated = tmp_path / "unrelated.zip"
    stale.write_bytes(b"old")
    unrelated.write_bytes(b"keep")
    old = time.time() - delivery._STALE_SECONDS - 60
    os.utime(stale, (old, old))
    os.utime(unrelated, (old, old))
    delivery._clean_stale()
    assert not stale.exists()
    assert unrelated.exists()
