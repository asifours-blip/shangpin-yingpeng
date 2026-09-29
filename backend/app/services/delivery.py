"""Deliver one approved platform version using its fixed review snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ImageAsset, User
from app.models.campaign import Campaign, ContentVariant, VariantReview
from app.services import storage
from app.services.publish_jobs import content_blockers


_MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4"}
_CHUNK = 1024 * 1024
_MAX_ASSET_BYTES = 2 * 1024 * 1024 * 1024
_MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
_MAX_COPY_BYTES = 2 * 1024 * 1024
_TEMP_DIR = Path(tempfile.gettempdir()) / "ops-delivery"
_TEMP_PREFIX = "ops-export-"
_STALE_SECONDS = 24 * 60 * 60


class DeliveryBlocked(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class DeliveryAsset:
    asset_id: int
    role: str
    position: int
    export_order: int
    object_key: str
    mime: str
    size_bytes: int
    filename: str


@dataclass(frozen=True)
class DeliverySnapshot:
    campaign_id: int
    platform: str
    version: int
    review_id: int
    reviewed_at: datetime
    started_at: datetime
    copy: dict
    assets: tuple[DeliveryAsset, ...]
    storyboard: dict | None = None


def _filename(export_order: int, role: str, mime: str) -> str:
    extension = _MIME_EXT.get(mime)
    if role in {"shot_first_frame", "shot_video"}:
        if (role == "shot_video") != (mime == "video/mp4") or extension is None:
            raise DeliveryBlocked("asset_invalid", "分镜资产的媒体类型不符合要求")
        return f"shots/{export_order // 2 + 1:02d}-{'video' if role == 'shot_video' else 'first-frame'}.{extension}"
    if extension is None or (role == "final_video") != (mime == "video/mp4"):
        raise DeliveryBlocked("asset_invalid", "审核资产的媒体类型不符合平台要求")
    return f"{export_order + 1:02d}-{'video' if role == 'final_video' else role}.{extension}"


def _descriptors(db: Session, user: User, review: VariantReview, platform: str) -> tuple[DeliveryAsset, ...]:
    entries = list(review.asset_order or [])
    storyboard_mode = platform == "douyin" and (review.qc_snapshot or {}).get("storyboard", {}).get("mode") == "reviewed_shots_v1"
    if storyboard_mode:
        if len(entries) != 6:
            raise DeliveryBlocked("assets_missing", "审核快照须包含三个镜头的首帧和视频")
        ordered = sorted(entries, key=lambda item: (item.get("position", -1), 0 if item.get("role") == "shot_first_frame" else 1))
        expected = ["shot_first_frame", "shot_video"] * 3
    else:
        if not 2 <= len(entries) <= 5:
            raise DeliveryBlocked("assets_missing", "审核快照缺少完整的媒体顺序")
        if platform == "douyin":
            ordered = sorted(entries, key=lambda item: {"cover": 0, "final_video": 1}.get(item.get("role"), 2))
        else:
            ordered = sorted(entries, key=lambda item: (0 if item.get("role") == "cover" else 1, item.get("position", -1)))
        expected = ["cover", "final_video"] if platform == "douyin" else ["cover", * (["card"] * (len(entries) - 1))]
    roles = [entry.get("role") for entry in ordered]
    if roles != expected:
        raise DeliveryBlocked("assets_missing", "审核快照的媒体角色或顺序不完整")
    positions = [entry.get("position") for entry in ordered]
    if any(not isinstance(position, int) or position < 0 for position in positions):
        raise DeliveryBlocked("asset_invalid", "审核快照的媒体顺序无效")
    if platform == "xiaohongshu" and positions != list(range(len(ordered))):
        raise DeliveryBlocked("asset_invalid", "内容卡的顺序不完整")
    if storyboard_mode and positions != [0, 0, 1, 1, 2, 2]:
        raise DeliveryBlocked("asset_invalid", "三镜头顺序不完整")
    if len({entry.get("asset_id") for entry in ordered}) != len(entries):
        raise DeliveryBlocked("asset_invalid", "审核快照含有重复的媒体资产")
    assets: list[DeliveryAsset] = []
    total_size = 0
    for export_order, entry in enumerate(ordered):
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, int):
            raise DeliveryBlocked("asset_invalid", "审核资产编号无效")
        asset = db.get(ImageAsset, asset_id)
        if asset is None or asset.owner_id != user.id or asset.bucket != settings.MINIO_BUCKET:
            raise DeliveryBlocked("asset_unreadable", "审核快照中的资产不可读取")
        if not 0 < asset.size_bytes <= _MAX_ASSET_BYTES:
            raise DeliveryBlocked("asset_invalid", "审核资产大小无效或超过导出上限")
        total_size += asset.size_bytes
        if total_size > _MAX_TOTAL_BYTES:
            raise DeliveryBlocked("asset_invalid", "素材总大小超过导出上限")
        role = entry["role"]
        position = entry["position"]
        assets.append(DeliveryAsset(
            asset_id=asset_id,
            role=role,
            position=position,
            export_order=export_order,
            object_key=asset.object_key,
            mime=asset.mime,
            size_bytes=asset.size_bytes,
            filename=_filename(export_order, role, asset.mime),
        ))
    return tuple(assets)


def freeze_delivery(
    db: Session, user: User, campaign_id: int, platform: str, version: int,
) -> DeliverySnapshot:
    """Fix current approval and asset descriptors under the campaign lock, then end the transaction."""
    try:
        if platform not in {"douyin", "xiaohongshu"}:
            raise DeliveryBlocked("platform_missing", "平台不存在", 404)
        db.execute(text("SET LOCAL lock_timeout = '8s'"))
        campaign = db.scalar(
            select(Campaign).where(Campaign.id == campaign_id, Campaign.owner_id == user.id)
            .with_for_update().execution_options(populate_existing=True)
        )
        if campaign is None:
            raise DeliveryBlocked("campaign_missing", "活动不存在", 404)
        variant = db.scalar(
            select(ContentVariant)
            .where(ContentVariant.campaign_id == campaign.id, ContentVariant.platform == platform)
            .order_by(ContentVariant.version.desc()).limit(1)
            .execution_options(populate_existing=True)
        )
        if variant is None:
            raise DeliveryBlocked("platform_missing", "平台内容不存在", 404)
        if variant.version != version:
            raise DeliveryBlocked("version_changed", "内容版本已经变化，请刷新后重试", 409)
        blockers = content_blockers(db, campaign, variant, check_storage=False)
        if blockers:
            first = blockers[0]
            raise DeliveryBlocked(first["code"], first["message"])
        review = db.scalar(
            select(VariantReview).where(
                VariantReview.variant_id == variant.id,
                VariantReview.version == version,
                VariantReview.decision == "approved",
            ).order_by(VariantReview.id.desc()).limit(1)
        )
        if review is None:
            raise DeliveryBlocked("review_mismatch", "当前版本没有有效的通过记录")
        assets = _descriptors(db, user, review, platform)
        return DeliverySnapshot(
            campaign_id=campaign.id,
            platform=platform,
            version=version,
            review_id=review.id,
            reviewed_at=review.reviewed_at,
            started_at=datetime.now(timezone.utc),
            copy=dict(review.copy_snapshot or {}),
            assets=assets,
            storyboard=(review.qc_snapshot or {}).get("storyboard") if platform == "douyin" else None,
        )
    finally:
        db.rollback()


def _clean_stale() -> None:
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - _STALE_SECONDS
    for path in _TEMP_DIR.glob(f"{_TEMP_PREFIX}*.zip"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _write_asset(bundle: zipfile.ZipFile, asset: DeliveryAsset) -> dict:
    digest = hashlib.sha256()
    size = 0
    try:
        with storage.open_stream(asset.object_key) as source, bundle.open(asset.filename, "w", force_zip64=True) as target:
            while chunk := source.read(_CHUNK):
                size += len(chunk)
                if size > asset.size_bytes or size > _MAX_ASSET_BYTES:
                    raise DeliveryBlocked("asset_changed", "审核资产的文件大小已变化，导出已停止")
                digest.update(chunk)
                try:
                    target.write(chunk)
                except OSError as exc:
                    raise DeliveryBlocked("archive_unavailable", "导出临时空间不可用，请稍后重试", 503) from exc
    except DeliveryBlocked:
        raise
    except Exception as exc:
        raise DeliveryBlocked("asset_unreadable", "审核资产无法从对象存储完整读取") from exc
    if size != asset.size_bytes:
        raise DeliveryBlocked("asset_changed", "审核资产的文件大小已变化，导出已停止")
    return {
        "file": asset.filename,
        "asset_id": asset.asset_id,
        "role": asset.role,
        "position": asset.position,
        "export_order": asset.export_order,
        "mime": asset.mime,
        "byte_size": size,
        "sha256": digest.hexdigest(),
    }


def build_archive(snapshot: DeliverySnapshot) -> Path:
    """Build a bounded disk-backed ZIP outside any database transaction or activity lock."""
    _clean_stale()
    descriptor, name = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=".zip", dir=_TEMP_DIR)
    os.close(descriptor)
    path = Path(name)
    try:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
            files = [_write_asset(bundle, asset) for asset in snapshot.assets]
            copied = snapshot.copy
            for filename, value in (
                ("title.txt", copied.get("title") or ""),
                ("body.txt", copied.get("body") or ""),
                ("hashtags.txt", "\n".join(copied.get("hashtags") or [])),
            ):
                payload = value.encode("utf-8")
                if len(payload) > _MAX_COPY_BYTES:
                    raise DeliveryBlocked("copy_too_large", "审核文案超过导出上限")
                bundle.writestr(filename, payload)
                files.append({
                    "file": filename,
                    "role": "copy",
                    "position": len(files),
                    "export_order": len(files),
                    "mime": "text/plain; charset=utf-8",
                    "byte_size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                })
            if snapshot.storyboard is not None:
                storyboard_bytes = json.dumps({
                    "mode": snapshot.storyboard.get("mode"),
                    "scope": "three_reviewed_shots_not_composited",
                    "shots": snapshot.storyboard.get("shots"),
                }, ensure_ascii=False, indent=2).encode("utf-8")
                if len(storyboard_bytes) > _MAX_COPY_BYTES:
                    raise DeliveryBlocked("storyboard_too_large", "分镜说明超过导出上限")
                bundle.writestr("storyboard.json", storyboard_bytes)
                files.append({
                    "file": "storyboard.json", "role": "storyboard", "position": len(files),
                    "export_order": len(files), "mime": "application/json",
                    "byte_size": len(storyboard_bytes), "sha256": hashlib.sha256(storyboard_bytes).hexdigest(),
                })
            bundle.writestr("manifest.json", json.dumps({
                "campaign_id": snapshot.campaign_id,
                "platform": snapshot.platform,
                "version": snapshot.version,
                "review_id": snapshot.review_id,
                "reviewed_at": snapshot.reviewed_at.isoformat(),
                "export_started_at": snapshot.started_at.isoformat(),
                "boundary": "current_approved_at_export_start",
                "media_scope": (
                    "three_reviewed_shots_not_composited" if snapshot.storyboard is not None
                    else "legacy_final_video" if snapshot.platform == "douyin" else "legacy_image_cards"
                ),
                "files": files,
            }, ensure_ascii=False, indent=2).encode("utf-8"))
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


class CleanupFileResponse(FileResponse):
    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


def archive_response(path: Path, snapshot: DeliverySnapshot) -> CleanupFileResponse:
    filename = f"campaign-{snapshot.campaign_id}-{snapshot.platform}-v{snapshot.version}.zip"
    return CleanupFileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        headers={"Cache-Control": "private, no-store"},
    )
