"""排期、领取和结果落账。外部调用不占着数据库事务。"""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.integrations.publish.base import PublishContext, PublishOutcome
from app.integrations.publish.credentials import CredentialError, resolve_credential
from app.integrations.publish.douyin import DouyinPublishAdapter
from app.integrations.publish.registry import get_adapter
from app.models import ImageAsset
from app.models.campaign import Campaign, ContentVariant, VariantReview
from app.models.publish import (
    MAYBE_SUBMITTED_STATUSES,
    PRE_SUBMIT_STATUSES,
    PublishJob,
)
from app.models.source import PlatformConnection
from app.models.user import User
from app.services import storage
from app.services.consumer_qc import screen_consumer_copy
from app.services.variant_review import (
    BLOCKING_QC,
    VersionConflict,
    _campaign,
    _commit_locked,
    _lock_campaign,
    _locked_current,
    _release,
    approval_blockers,
)

LEASE = timedelta(seconds=120)
MAYBE_SUBMITTED_NOTICE = "可能已经提交，不能撤回平台上的内容"
ACTIVE = (
    "scheduled",
    "needs_reconfirm",
    "submitting",
    "uploaded",
    "create_accepted",
    "publish_unknown",
)


class PublishBlocked(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def invalidate_unsubmitted(db: Session, variant_id: int) -> int:
    """只失效还没进入 submitting 的任务。已提交的快照留着对账。"""
    rows = list(
        db.scalars(
            select(PublishJob)
            .where(
                PublishJob.variant_id == variant_id,
                PublishJob.status.in_(PRE_SUBMIT_STATUSES),
            )
            .with_for_update()
        ).all()
    )
    for row in rows:
        row.status = "invalidated"
        row.error_code = "review_changed"
        row.error_message = "版本或批准已变化，未提交的发布任务已失效"
        row.claim_token = None
        row.lease_until = None
    return len(rows)


def submission_notice(db: Session, campaign_id: int, platform: str | None = None) -> str | None:
    stmt = select(PublishJob.id).where(
        PublishJob.campaign_id == campaign_id,
        PublishJob.status.in_(MAYBE_SUBMITTED_STATUSES),
    )
    if platform is not None:
        stmt = stmt.where(PublishJob.platform == platform)
    if db.scalar(stmt) is None:
        return None
    return MAYBE_SUBMITTED_NOTICE


def _latest_approval(db: Session, variant: ContentVariant) -> VariantReview | None:
    return db.scalar(
        select(VariantReview)
        .where(
            VariantReview.variant_id == variant.id,
            VariantReview.version == variant.version,
            VariantReview.decision == "approved",
        )
        .order_by(VariantReview.id.desc())
        .execution_options(populate_existing=True)
    )


def _asset_rows(db: Session, variant_id: int) -> list[dict]:
    from app.models.campaign import VariantAsset

    rows = db.scalars(
        select(VariantAsset).where(VariantAsset.variant_id == variant_id).order_by(VariantAsset.position.asc())
    ).all()
    return [
        {"asset_id": row.asset_id, "role": row.role, "position": row.position}
        for row in rows
    ]


def _identity(items: list[dict]) -> list[dict]:
    return [
        {"asset_id": item.get("asset_id"), "role": item.get("role"), "position": item.get("position")}
        for item in items
    ]


def _media_entries(db: Session, review: VariantReview) -> list[dict]:
    entries = []
    for entry in list(review.asset_order or []):
        asset = db.get(ImageAsset, int(entry["asset_id"]))
        if asset is None:
            raise PublishBlocked("asset_unreadable", "审核快照里的资产已经找不到")
        entries.append({**entry, "object_key": asset.object_key})
    return entries


def _read_media(entries: list[dict]) -> tuple[list[dict], tuple[bytes, ...]]:
    """调用前结束数据库事务；返回校验值和后续实际使用的同一份字节。"""
    frozen = []
    payloads = []
    for entry in entries:
        try:
            if not storage.object_exists(entry["object_key"]):
                raise PublishBlocked("asset_unreadable", "审核快照里的文件不存在")
            payload = storage.get_bytes(entry["object_key"])
        except PublishBlocked:
            raise
        except Exception as exc:
            raise PublishBlocked("asset_unreadable", "审核快照里的文件读不出来") from exc
        if not payload:
            raise PublishBlocked("asset_unreadable", "审核快照里的文件是空的或读不出来")
        frozen.append(
            {
                "asset_id": int(entry["asset_id"]),
                "role": entry.get("role"),
                "position": entry.get("position"),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "byte_size": len(payload),
            }
        )
        payloads.append(payload)
    return frozen, tuple(payloads)


def _qc_block(review: VariantReview) -> str | None:
    qc = dict(review.qc_snapshot or {})
    if qc.get("qc_issue") in BLOCKING_QC or qc.get("unplaced"):
        return "qc_failed"
    snap = dict(review.copy_snapshot or {})
    issues = screen_consumer_copy(
        title=snap.get("title") or "",
        body=snap.get("body") or "",
        hashtags=list(snap.get("hashtags") or []),
        facts=dict(review.fact_snapshot or {}),
    )["issues"]
    if issues:
        return "qc_failed"
    return None


def content_blockers(
    db: Session, campaign: Campaign, variant: ContentVariant, *, check_storage: bool = True,
) -> list[dict]:
    """资格只看这一侧的当前版本。活动 failed 不在这里。"""
    blockers: list[dict] = []
    if variant.status != "approved":
        blockers.append({"code": "not_approved", "message": "这一版还没有批准"})
        return blockers
    review = _latest_approval(db, variant)
    if review is None:
        blockers.append({"code": "review_mismatch", "message": "没有和当前版本一致的通过记录"})
    else:
        if review.fact_version_id != variant.fact_version_id:
            blockers.append({"code": "review_mismatch", "message": "事实版本已经和审核快照不一致"})
        snap = dict(review.copy_snapshot or {})
        if snap.get("title") != variant.title or snap.get("body") != variant.body:
            blockers.append({"code": "review_mismatch", "message": "文案已经和审核快照不一致"})
        if list(snap.get("hashtags") or []) != list(variant.hashtags or []):
            blockers.append({"code": "review_mismatch", "message": "话题已经和审核快照不一致"})
        live_assets = _identity(_asset_rows(db, variant.id))
        reviewed = _identity(list(review.asset_order or []))
        if live_assets != reviewed:
            blockers.append({"code": "review_mismatch", "message": "资产顺序已经和审核快照不一致"})
        if _qc_block(review):
            blockers.append({"code": "qc_failed", "message": "质检未通过，不能安排发布"})
    blockers.extend(approval_blockers(db, campaign, variant, check_storage=check_storage))
    deduped: list[dict] = []
    seen: set[str] = set()
    for item in blockers:
        if item["code"] in seen:
            continue
        seen.add(item["code"])
        deduped.append(item)
    return deduped


def _connection(db: Session, user: User, connection_id: int | None, platform: str) -> PlatformConnection | None:
    if connection_id is None:
        return None
    row = db.get(PlatformConnection, connection_id)
    if row is None or row.owner_id != user.id or row.purpose != "publish" or row.platform != platform:
        _release(db, PublishBlocked("account_missing", "发布账号不存在"))
    return row


def _capability(adapter, connection: PlatformConnection | None) -> list[str]:
    missing = adapter.check_capability(connection)
    if adapter.catalog().get("implemented") is False:
        missing = [*missing, "待接入：发布适配器未接入"]
    return missing


def _readiness(platform: str, missing: list[str], *, content_ready: bool = True) -> str:
    if not content_ready:
        return "pending_connection"
    adapter = get_adapter(platform)
    catalog = adapter.catalog()
    if catalog.get("server_publish") is False:
        return "approved_ready_to_publish"
    if missing:
        return "pending_connection"
    return "ready"


def platform_view(db: Session, user: User, campaign: Campaign, platform: str) -> dict:
    from app.services.variant_review import _current

    variant = _current(db, campaign.id, platform)
    review = _latest_approval(db, variant) if variant.status == "approved" else None
    adapter = get_adapter(platform)
    content = content_blockers(db, campaign, variant)
    connections = list(
        db.scalars(
            select(PlatformConnection)
            .where(
                PlatformConnection.owner_id == user.id,
                PlatformConnection.platform == platform,
                PlatformConnection.purpose == "publish",
            )
            .order_by(PlatformConnection.id.asc())
        ).all()
    )
    account_missing = _capability(adapter, None)
    return {
        "platform": platform,
        "variant_id": variant.id,
        "version": variant.version,
        "variant_status": variant.status,
        "review_id": None if review is None else review.id,
        "copy_snapshot": {} if review is None else dict(review.copy_snapshot or {}),
        "content_blockers": content,
        "content_ready": not content,
        "readiness": "content_blocked" if content else _readiness(platform, account_missing),
        "connection_state": "待连接" if account_missing else "已连接",
        "missing": account_missing,
        "catalog": adapter.catalog(),
        "connections": [
            {
                "id": row.id,
                "external_account_id": row.external_account_id,
                "status": row.status,
                "scope_set": list(row.scope_set or []),
                "expires_at": row.expires_at,
                "missing": _capability(adapter, row),
                "readiness": "content_blocked" if content else _readiness(platform, _capability(adapter, row)),
            }
            for row in connections
        ],
        "campaign_status": campaign.status,
    }


def _active_job(
    db: Session,
    *,
    owner_id: int,
    platform: str,
    variant_id: int,
    version: int,
    connection_id: int | None,
) -> PublishJob | None:
    stmt = select(PublishJob).where(
        PublishJob.owner_id == owner_id,
        PublishJob.platform == platform,
        PublishJob.variant_id == variant_id,
        PublishJob.version == version,
        PublishJob.status.in_(ACTIVE),
    )
    if connection_id is None:
        stmt = stmt.where(PublishJob.connection_id.is_(None))
    else:
        stmt = stmt.where(PublishJob.connection_id == connection_id)
    return db.scalar(stmt.order_by(PublishJob.id.asc()))


def _same_schedule(
    existing: PublishJob,
    *,
    campaign_id: int,
    platform: str,
    variant_id: int,
    version: int,
    connection_id: int | None,
    scheduled_at: datetime,
) -> bool:
    return (
        existing.campaign_id == campaign_id
        and existing.platform == platform
        and existing.variant_id == variant_id
        and existing.version == version
        and existing.connection_id == connection_id
        and _aware(existing.scheduled_at) == scheduled_at
    )


def _require_same_schedule(existing: PublishJob, **requested) -> PublishJob:
    if not _same_schedule(existing, **requested):
        raise PublishBlocked("idempotency_conflict", "这个幂等键已经用在不同的发布参数上")
    return existing


def schedule_job(
    db: Session,
    user: User,
    campaign_id: int,
    platform: str,
    *,
    expected_version: int,
    scheduled_at: datetime,
    connection_id: int | None,
    idempotency_key: str | None,
) -> PublishJob:
    if platform not in {"douyin", "xiaohongshu"}:
        _release(db, PublishBlocked("platform_unknown", "只安排抖音或小红书"))
    when = _aware(scheduled_at)
    if when <= _now():
        _release(db, PublishBlocked("schedule_past", "排期要选一个还没到的时间"))
    # 先取不可变的审核输入，再结束事务做对象存储 I/O。
    preview_campaign = _campaign(db, user, campaign_id)
    from app.services.variant_review import _current

    preview_variant = _current(db, campaign_id, platform)
    if preview_variant.version != expected_version:
        _release(db, VersionConflict("版本已变化"))
    preview_blockers = content_blockers(db, preview_campaign, preview_variant, check_storage=False)
    if preview_blockers:
        _release(db, PublishBlocked(preview_blockers[0]["code"], preview_blockers[0]["message"]))
    preview_review = _latest_approval(db, preview_variant)
    if preview_review is None:
        _release(db, PublishBlocked("review_mismatch", "没有和当前版本一致的通过记录"))
    preview_review_id = preview_review.id
    try:
        entries = _media_entries(db, preview_review)
    except PublishBlocked as exc:
        _release(db, exc)
    db.rollback()
    assets, _ = _read_media(entries)

    campaign = _lock_campaign(db, user, campaign_id)
    variant = _locked_current(db, campaign_id, platform, expected_version)
    blockers = content_blockers(db, campaign, variant, check_storage=False)
    if blockers:
        _release(db, PublishBlocked(blockers[0]["code"], blockers[0]["message"]))
    review = _latest_approval(db, variant)
    if review is None or review.id != preview_review_id or _media_entries(db, review) != entries:
        _release(db, PublishBlocked("review_mismatch", "没有和当前版本一致的通过记录"))
    connection = _connection(db, user, connection_id, platform)
    adapter = get_adapter(platform)
    missing = _capability(adapter, connection)
    connection_key = None if connection is None else connection.id
    key = (idempotency_key or "").strip() or None
    requested = dict(
        campaign_id=campaign.id, platform=platform, variant_id=variant.id,
        version=variant.version, connection_id=connection_key, scheduled_at=when,
    )
    if key:
        existing_key = db.scalar(
            select(PublishJob).where(PublishJob.owner_id == user.id, PublishJob.idempotency_key == key)
        )
        if existing_key is not None:
            try:
                _require_same_schedule(existing_key, **requested)
            except PublishBlocked as exc:
                _release(db, exc)
            _commit_locked(db)
            return existing_key
    existing = _active_job(
        db,
        owner_id=user.id,
        platform=platform,
        variant_id=variant.id,
        version=variant.version,
        connection_id=connection_key,
    )
    if existing is not None:
        _commit_locked(db)
        return existing
    job = PublishJob(
        owner_id=user.id,
        campaign_id=campaign.id,
        platform=platform,
        connection_id=connection_key,
        variant_id=variant.id,
        version=variant.version,
        review_id=review.id,
        scheduled_at=when,
        status="scheduled",
        copy_snapshot=dict(review.copy_snapshot or {}),
        asset_order=assets,
        missing_requirements=missing,
        readiness=_readiness(platform, missing),
        idempotency_key=key,
        result={},
        target_open_id=connection.external_account_id if connection else None,
        phase="pending_cover",
    )
    db.add(job)
    try:
        _commit_locked(db)
    except VersionConflict:
        raced_key = db.scalar(
            select(PublishJob).where(PublishJob.owner_id == user.id, PublishJob.idempotency_key == key)
        ) if key else None
        if raced_key is not None:
            return _require_same_schedule(raced_key, **requested)
        raced = _active_job(
            db,
            owner_id=user.id,
            platform=platform,
            variant_id=variant.id,
            version=variant.version,
            connection_id=connection_key,
        )
        if raced is None:
            raise
        return raced
    db.refresh(job)
    return job


def list_jobs(db: Session, user: User, campaign_id: int) -> list[PublishJob]:
    campaign = _campaign(db, user, campaign_id)
    return list(
        db.scalars(
            select(PublishJob)
            .where(PublishJob.campaign_id == campaign.id, PublishJob.owner_id == user.id)
            .order_by(PublishJob.id.asc())
        ).all()
    )


def publish_desk(db: Session, user: User, campaign_id: int) -> dict:
    campaign = _campaign(db, user, campaign_id)
    platforms = []
    for platform in ("douyin", "xiaohongshu"):
        try:
            platforms.append(platform_view(db, user, campaign, platform))
        except Exception as exc:
            from app.services.campaign_service import CampaignNotFound

            if isinstance(exc, CampaignNotFound):
                continue
            raise
    return {
        "campaign_id": campaign.id,
        "campaign_status": campaign.status,
        "timezone_storage": "UTC",
        "platforms": platforms,
        "jobs": [job_public(row) for row in list_jobs(db, user, campaign_id)],
    }


def cancel_job(db: Session, user: User, job_id: int) -> PublishJob:
    job = db.get(PublishJob, job_id)
    if job is None or job.owner_id != user.id:
        _release(db, PublishBlocked("job_missing", "发布任务不存在"))
    _lock_campaign(db, user, job.campaign_id)
    locked = db.scalar(
        select(PublishJob).where(PublishJob.id == job.id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        _release(db, PublishBlocked("job_missing", "发布任务不存在"))
    if locked.status in MAYBE_SUBMITTED_STATUSES:
        _release(db, PublishBlocked("maybe_submitted", MAYBE_SUBMITTED_NOTICE))
    if locked.status not in PRE_SUBMIT_STATUSES:
        _release(db, PublishBlocked("cancel_closed", "这个任务已经不能取消"))
    locked.status = "cancelled"
    locked.error_code = None
    locked.error_message = None
    locked.claim_token = None
    locked.lease_until = None
    _commit_locked(db)
    db.refresh(locked)
    return locked


def reconfirm_job(db: Session, user: User, job_id: int, *, scheduled_at: datetime) -> PublishJob:
    job = db.get(PublishJob, job_id)
    if job is None or job.owner_id != user.id:
        _release(db, PublishBlocked("job_missing", "发布任务不存在"))
    _lock_campaign(db, user, job.campaign_id)
    locked = db.scalar(
        select(PublishJob).where(PublishJob.id == job.id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or locked.status != "needs_reconfirm":
        _release(db, PublishBlocked("reconfirm_closed", "只有过期后待确认的任务可以重新确认"))
    reason = _eligible_for_submit(db, locked)
    if reason is not None:
        _release(db, PublishBlocked(reason, "审核或版本已变化，不能重新确认旧任务"))
    adapter = get_adapter(locked.platform)
    if adapter.catalog().get("server_publish") is False:
        _release(
            db,
            PublishBlocked(
                "approved_ready_to_publish",
                "小红书没有可核验的发布接口，重新确认也不会代发",
            ),
        )
    when = _aware(scheduled_at)
    if when <= _now():
        _release(db, PublishBlocked("schedule_past", "重新确认要选一个还没到的时间"))
    connection = db.get(PlatformConnection, locked.connection_id) if locked.connection_id else None
    missing = _capability(adapter, connection)
    locked.scheduled_at = when
    locked.target_open_id = connection.external_account_id if connection else None
    if locked.platform == "douyin" and locked.target_open_id:
        locked.phase = "pending_cover"
    locked.missing_requirements = missing
    locked.readiness = _readiness(locked.platform, missing)
    locked.claim_token = None
    locked.lease_until = None
    if missing or locked.readiness != "ready":
        locked.status = "needs_reconfirm"
        locked.error_code = "needs_reconfirm"
        locked.error_message = "仍然待连接。过期排期不会自动补发，补齐后再重新确认。"
    else:
        locked.status = "scheduled"
        locked.error_code = None
        locked.error_message = None
    _commit_locked(db)
    db.refresh(locked)
    return locked


def revoke_approval(db: Session, user: User, campaign_id: int, platform: str, *, expected_version: int) -> dict:
    campaign = _lock_campaign(db, user, campaign_id)
    variant = _locked_current(db, campaign_id, platform, expected_version)
    if variant.status != "approved":
        _release(db, PublishBlocked("not_approved", "这一版不是通过状态"))
    invalidate_unsubmitted(db, variant.id)
    variant.status = "needs_review"
    in_flight = db.scalar(
        select(PublishJob.id).where(
            PublishJob.variant_id == variant.id,
            PublishJob.status.in_(MAYBE_SUBMITTED_STATUSES),
        )
    )
    from app.services.variant_review import _sync_campaign

    _sync_campaign(db, campaign)
    _commit_locked(db)
    db.refresh(variant)
    return {
        "variant_id": variant.id,
        "version": variant.version,
        "status": variant.status,
        "maybe_submitted": in_flight is not None,
        "notice": None
        if in_flight is None
        else "本地批准已撤回。已经进入提交的任务仍按原快照对账，" + MAYBE_SUBMITTED_NOTICE,
    }


def _eligible_for_submit(
    db: Session, job: PublishJob, *, media_hashes: list[dict] | None = None,
) -> str | None:
    campaign = db.get(Campaign, job.campaign_id, populate_existing=True)
    if campaign is None:
        return "review_mismatch"
    current_id = db.scalar(
        select(ContentVariant.id)
        .where(ContentVariant.campaign_id == job.campaign_id, ContentVariant.platform == job.platform)
        .order_by(ContentVariant.version.desc())
        .limit(1)
    )
    if current_id != job.variant_id:
        return "not_approved"
    variant = db.get(ContentVariant, job.variant_id, populate_existing=True)
    review = db.get(VariantReview, job.review_id, populate_existing=True)
    if variant is None or variant.version != job.version or variant.status != "approved":
        return "not_approved"
    latest = None if variant is None else _latest_approval(db, variant)
    if (
        review is None or review.variant_id != variant.id or review.decision != "approved"
        or review.version != job.version or latest is None or latest.id != review.id
    ):
        return "review_mismatch"
    snap = dict(review.copy_snapshot or {})
    if snap != dict(job.copy_snapshot or {}):
        return "review_mismatch"
    if (variant.title, variant.body, list(variant.hashtags or [])) != (
        snap.get("title"),
        snap.get("body"),
        list(snap.get("hashtags") or []),
    ):
        return "review_mismatch"
    if _identity(_asset_rows(db, variant.id)) != _identity(list(review.asset_order or [])):
        return "review_mismatch"
    if _qc_block(review):
        return "qc_failed"
    blockers = content_blockers(db, campaign, variant, check_storage=False)
    if blockers:
        return blockers[0]["code"]
    if media_hashes is not None and media_hashes != list(job.asset_order or []):
        return "snapshot_drift"
    return None


def _park_unready(job: PublishJob, missing: list[str], message: str) -> None:
    job.status = "needs_reconfirm"
    job.missing_requirements = missing
    if get_adapter(job.platform).catalog().get("server_publish") is False:
        job.readiness = "approved_ready_to_publish"
        job.error_code = "approved_ready_to_publish"
    else:
        job.readiness = job.readiness if job.readiness != "ready" else "pending_connection"
        job.error_code = "needs_reconfirm"
    job.error_message = message
    job.claim_token = None
    job.lease_until = None


def _due_candidate():
    retry_ready = or_(PublishJob.next_attempt_at.is_(None), PublishJob.next_attempt_at <= func.clock_timestamp())
    return or_(
        and_(PublishJob.status == "scheduled", PublishJob.scheduled_at <= func.clock_timestamp()),
        and_(PublishJob.status == "uploaded", PublishJob.phase.in_(("cover_uploaded", "video_uploaded")), retry_ready),
        and_(PublishJob.status == "submitting", PublishJob.claim_token.is_(None),
             PublishJob.lease_until.is_(None),
             PublishJob.phase.in_(("pending_cover", "cover_uploaded", "video_uploaded")), retry_ready),
    )


def claim_due(db: Session, *, allow_unready: bool = False) -> tuple[PublishJob, str] | None:
    """锁活动行后再改状态。返回的任务已经进入 submitting，调用方这时才能出网。"""
    db.execute(text("SET LOCAL lock_timeout = '8s'"))
    candidate_id = db.scalar(
        select(PublishJob.id)
        .where(_due_candidate())
        .order_by(PublishJob.id.asc())
        .limit(1)
    )
    if candidate_id is None:
        db.rollback()
        return None
    preview = db.get(PublishJob, candidate_id)
    if preview is None:
        db.rollback()
        return None
    campaign_id = preview.campaign_id
    review = db.get(VariantReview, preview.review_id)
    try:
        entries = [] if review is None else _media_entries(db, review)
        entry_error = None
    except PublishBlocked as exc:
        entries = []
        entry_error = exc.code
    db.rollback()
    try:
        media_hashes, media_bytes = _read_media(entries)
        media_error = entry_error or (None if review is not None else "review_mismatch")
    except PublishBlocked as exc:
        media_hashes, media_bytes, media_error = [], (), exc.code

    db.execute(text("SET LOCAL lock_timeout = '8s'"))
    campaign = db.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if campaign is None:
        db.rollback()
        return None
    job = db.scalar(
        select(PublishJob).where(PublishJob.id == candidate_id, _due_candidate())
        .with_for_update().execution_options(populate_existing=True)
    )
    if job is None:
        db.rollback()
        return None
    adapter = get_adapter(job.platform)
    connection = db.get(PlatformConnection, job.connection_id) if job.connection_id else None
    if job.platform == "douyin" and (job.phase == "legacy_unknown" or not job.target_open_id):
        _park_unready(job, ["待确认：旧排期没有冻结的发布账号"], "旧排期未绑定目标账号，必须重新确认，不能自动发送")
        _commit_locked(db)
        return None
    if connection is not None and (
        connection.owner_id != job.owner_id or connection.purpose != "publish" or connection.platform != job.platform
        or (job.target_open_id is not None and connection.external_account_id != job.target_open_id)
    ):
        job.status = "publish_unknown" if job.status != "scheduled" else "failed"
        job.error_code = "wrong_account"
        job.error_message = "发布账号绑定已变化，没有向平台继续发送"
        _commit_locked(db)
        return None
    missing = _capability(adapter, connection)
    catalog = adapter.catalog()
    server_publish = catalog.get("server_publish") is not False
    implemented = catalog.get("implemented") is not False
    resumed = job.status != "scheduled"
    if resumed and (not server_publish or not implemented or (not allow_unready and missing)):
        job.missing_requirements = missing
        job.error_code = "stage_blocked"
        job.error_message = "已上传的产物仍保留；连接恢复后才继续下一阶段，未向平台重发"
        job.next_attempt_at = db.scalar(select(func.clock_timestamp())) + timedelta(seconds=60)
        _commit_locked(db)
        return None
    if not server_publish or not implemented or (not allow_unready and (job.readiness != "ready" or missing)):
        if not server_publish:
            message = "小红书没有可核验的发布接口，保持待发布，没有向外提交"
        elif job.readiness != "ready" and not missing:
            message = "凭证补上之前这张排期已经过期，不会自动补发，需要重新确认"
        else:
            message = "排期已到但还不能提交。补齐凭证后必须重新确认，不会自动补发。"
        _park_unready(job, missing, message)
        _commit_locked(db)
        return None
    reason = media_error or (
        "snapshot_drift" if resumed and media_hashes != list(job.asset_order or [])
        else None if resumed else _eligible_for_submit(db, job, media_hashes=media_hashes)
    )
    if resumed and reason is None and job.phase in {"cover_uploaded", "video_uploaded"}:
        frozen = {entry.get("role"): entry.get("sha256") for entry in job.asset_order or []}
        if not job.cover_image_id or not job.cover_uploaded_at or job.cover_sha256 != frozen.get("cover"):
            reason = "artifact_unverified"
        elif job.phase == "video_uploaded" and (
            not job.video_upload_id or not job.video_uploaded_at
            or job.video_sha256 != frozen.get("final_video")
        ):
            reason = "artifact_unverified"
    if reason is None and review is not None and not resumed:
        try:
            if _media_entries(db, review) != entries:
                reason = "snapshot_drift"
        except PublishBlocked as exc:
            reason = exc.code
    if reason is not None:
        job.status = "publish_unknown" if resumed else "invalidated" if reason == "not_approved" else "failed"
        job.error_code = reason
        job.error_message = "提交前资格不再成立，没有向外发送"
        job.claim_token = None
        job.lease_until = None
        _commit_locked(db)
        return None
    token = secrets.token_hex(16)
    job.status = "submitting"
    job.claim_token = token
    job.local_request_id = job.local_request_id or secrets.token_hex(16)
    job.lease_until = db.scalar(select(func.clock_timestamp())) + LEASE
    job.attempt = int(job.attempt or 0) + 1
    job.next_attempt_at = None
    job.missing_requirements = missing
    _commit_locked(db)
    db.refresh(job)
    job._asset_bytes = media_bytes
    return job, token


def renew_lease(db: Session, job_id: int, token: str, *, commit: bool = True) -> bool:
    """续租。令牌不对，或租约已经失效，就不能续，也不能被另一个工人接去重提。"""
    locked_id = db.scalar(select(PublishJob.id).where(PublishJob.id == job_id).with_for_update())
    if locked_id is None:
        if commit:
            db.commit()
        return False
    row = db.execute(
        text(
            """
            UPDATE publish_jobs
            SET lease_until = clock_timestamp() + make_interval(secs => :lease_seconds)
            WHERE id = :job_id
              AND claim_token = :token
              AND status = 'submitting'
              AND lease_until IS NOT NULL
              AND lease_until > clock_timestamp()
            RETURNING id
            """
        ),
        {"job_id": job_id, "token": token, "lease_seconds": LEASE.total_seconds()},
    ).first()
    if commit:
        db.commit()
    return row is not None


def _reject_published(outcome: PublishOutcome) -> PublishOutcome:
    if outcome.kind != "published":
        return outcome
    return PublishOutcome(
        kind="unknown",
        provider_request_id=outcome.provider_request_id,
        provider_video_id=outcome.provider_video_id,
        error_code="publish_unknown",
        error_message="上传或创建成功不能当成已发布。结果不明，不会自动重新提交",
        result=dict(outcome.result or {}),
    )


def apply_outcome(db: Session, job_id: int, token: str, outcome: PublishOutcome) -> PublishJob | None:
    outcome = _reject_published(outcome)
    values = {
        "result": dict(outcome.result or {}),
        "claim_token": None,
        "lease_until": None,
    }
    if outcome.provider_request_id:
        values["provider_request_id"] = outcome.provider_request_id
    if outcome.provider_video_id:
        values["provider_video_id"] = outcome.provider_video_id
    if outcome.kind == "uploaded":
        values.update(status="uploaded", error_code=None, error_message="文件已上传，还没有创建内容，也还没有发布")
    elif outcome.kind == "create_accepted":
        values.update(status="create_accepted", error_code=None, error_message="创建请求已接受。这不是公开发布，不能当成 published")
    elif outcome.kind == "unknown":
        values.update(status="publish_unknown", error_code=outcome.error_code or "publish_unknown", error_message=outcome.error_message or "提交结果不明，不会自动重新提交")
    elif outcome.kind == "failed":
        values.update(status="failed", error_code=outcome.error_code or "platform_failed", error_message=outcome.error_message or "平台拒绝了这次提交")
    else:
        values.update(status="needs_reconfirm", error_code=outcome.error_code or "pending_connection", error_message=outcome.error_message or "没有向外提交")
    db.execute(text("SET LOCAL lock_timeout = '8s'"))
    locked_id = db.scalar(select(PublishJob.id).where(PublishJob.id == job_id).with_for_update())
    if locked_id is None:
        db.rollback()
        return None
    changed = db.scalar(
        update(PublishJob)
        .where(
            PublishJob.id == job_id,
            PublishJob.claim_token == token,
            PublishJob.status == "submitting",
            PublishJob.lease_until.is_not(None),
            PublishJob.lease_until > func.clock_timestamp(),
        )
        .values(**values)
        .returning(PublishJob.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        db.rollback()
        return None
    try:
        _commit_locked(db)
    except IntegrityError:
        db.rollback()
        return None
    return db.get(PublishJob, job_id, populate_existing=True)


def execute_claimed(db: Session, job: PublishJob, token: str, outcome: PublishOutcome) -> PublishJob | None:
    """测试和工人都走这里。outcome 由调用方在事务外准备好。"""
    return apply_outcome(db, job.id, token, outcome)


def _context(job: PublishJob) -> PublishContext:
    return PublishContext(
        platform=job.platform,
        copy_snapshot=dict(job.copy_snapshot or {}),
        asset_order=list(job.asset_order or []),
        local_request_id=job.local_request_id or "",
        connection_id=job.connection_id,
        provider_request_id=job.provider_request_id,
        asset_bytes=getattr(job, "_asset_bytes", ()),
        target_open_id=job.target_open_id,
        cover_image_id=job.cover_image_id,
        video_upload_id=job.video_upload_id,
    )


def _unknown_from(exc: Exception) -> PublishOutcome:
    return PublishOutcome(
        kind="unknown",
        error_code="publish_unknown",
        error_message=f"提交结果不明，不会自动重新提交：{type(exc).__name__}",
    )


def _claim_alive(job_id: int, token: str) -> bool:
    with SessionLocal() as check:
        check.execute(text("SET LOCAL statement_timeout = '3s'"))
        return bool(check.scalar(
            select(PublishJob.id).where(
                PublishJob.id == job_id,
                PublishJob.claim_token == token,
                PublishJob.status == "submitting",
                PublishJob.lease_until > func.clock_timestamp(),
            )
        ))


def _external_with_lease(job_id: int, token: str, call) -> PublishOutcome | None:
    stopped = threading.Event()
    lost = threading.Event()

    def alive() -> bool:
        if lost.is_set():
            return False
        try:
            return _claim_alive(job_id, token)
        except Exception:
            lost.set()
            return False

    def heartbeat() -> None:
        interval = max(0.1, LEASE.total_seconds() / 3)
        while not stopped.wait(interval):
            try:
                with SessionLocal() as renewal:
                    renewal.execute(text("SET LOCAL lock_timeout = '2s'"))
                    renewal.execute(text("SET LOCAL statement_timeout = '3s'"))
                    if not renew_lease(renewal, job_id, token):
                        lost.set()
                        return
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        outcome = call(alive)
    except Exception as exc:  # noqa: BLE001
        outcome = _unknown_from(exc)
    finally:
        stopped.set()
        thread.join(timeout=4)
        if thread.is_alive():
            lost.set()
    return outcome if alive() else None


_STAGES = {
    "pending_cover": ("cover", "cover_intent"),
    "cover_uploaded": ("video", "video_intent"),
    "video_uploaded": ("create", "create_intent"),
}


def _stage_digest(ctx: PublishContext, role: str) -> str | None:
    matches = [entry.get("sha256") for entry in ctx.asset_order if entry.get("role") == role]
    return matches[0] if len(matches) == 1 else None


def _stage_intent(db: Session, job_id: int, token: str, expected: str, stage: str) -> bool:
    """先取得任务行锁，随后才按数据库实时时钟核租约并提交意图。"""
    db.execute(text("SET LOCAL lock_timeout = '8s'"))
    if db.scalar(select(PublishJob.id).where(PublishJob.id == job_id).with_for_update()) is None:
        db.rollback()
        return False
    changed = db.scalar(
        update(PublishJob).where(
            PublishJob.id == job_id, PublishJob.status == "submitting",
            PublishJob.phase == expected, PublishJob.claim_token == token,
            PublishJob.lease_until > func.clock_timestamp(),
        ).values(phase=f"{stage}_intent", **{f"{stage}_intent_at": func.clock_timestamp()})
        .returning(PublishJob.id).execution_options(synchronize_session=False)
    )
    if changed is None:
        db.rollback()
        return False
    db.commit()
    return True


def _stage_outcome(
    db: Session, job_id: int, token: str, stage: str, ctx: PublishContext, outcome: PublishOutcome,
) -> bool:
    db.execute(text("SET LOCAL lock_timeout = '8s'"))
    if db.scalar(select(PublishJob.id).where(PublishJob.id == job_id).with_for_update()) is None:
        db.rollback()
        return False
    values = {
        "claim_token": None, "lease_until": None,
        "error_code": outcome.error_code, "error_message": outcome.error_message,
        "result": dict(outcome.result or {}),
    }
    if outcome.kind == "cover_uploaded" and stage == "cover" and outcome.cover_image_id:
        values.update(
            status="uploaded", phase="cover_uploaded", cover_image_id=outcome.cover_image_id,
            cover_sha256=_stage_digest(ctx, "cover"), cover_log_id=outcome.log_id,
            cover_uploaded_at=func.clock_timestamp(), error_code=None, error_message=None,
        )
    elif outcome.kind == "video_uploaded" and stage == "video" and outcome.video_upload_id:
        values.update(
            status="uploaded", phase="video_uploaded", video_upload_id=outcome.video_upload_id,
            video_sha256=_stage_digest(ctx, "final_video"), video_log_id=outcome.log_id,
            video_uploaded_at=func.clock_timestamp(), error_code=None, error_message=None,
        )
    elif outcome.kind == "create_accepted" and stage == "create" and outcome.content_item_id:
        values.update(
            status="create_accepted", phase="done", content_item_id=outcome.content_item_id,
            content_video_id=outcome.content_video_id, create_log_id=outcome.log_id,
            create_accepted_at=func.clock_timestamp(), error_code=None,
            error_message="创建请求已接受，平台仍可能审核；这不是公开发布",
        )
    elif outcome.kind == "not_ready":
        prior = {"cover": "pending_cover", "video": "cover_uploaded", "create": "video_uploaded"}[stage]
        values.update(status="submitting", phase=prior, error_code=outcome.error_code or "stage_blocked",
                      error_message=outcome.error_message or "账号待修复；已有上传产物保留",
                      next_attempt_at=func.clock_timestamp() + text("interval '60 seconds'"))
    elif outcome.kind == "failed":
        values.update(status="failed", error_code=outcome.error_code or f"{stage}_rejected")
    else:
        values.update(status="publish_unknown", error_code=outcome.error_code or f"{stage}_unknown",
                      error_message=outcome.error_message or "平台结果不明，停止自动重试")
    if outcome.log_id and f"{stage}_log_id" not in values:
        values[f"{stage}_log_id"] = outcome.log_id
    changed = db.scalar(
        update(PublishJob).where(
            PublishJob.id == job_id, PublishJob.status == "submitting",
            PublishJob.phase == f"{stage}_intent", PublishJob.claim_token == token,
            PublishJob.lease_until > func.clock_timestamp(),
        ).values(**values).returning(PublishJob.id).execution_options(synchronize_session=False)
    )
    if changed is None:
        db.rollback()
        return False
    db.commit()
    return True


def _run_douyin_stage(db: Session, job: PublishJob, token: str, adapter: DouyinPublishAdapter) -> int:
    job_id, owner_id, connection_id = job.id, job.owner_id, job.connection_id
    expected_phase = job.phase
    stage_info = _STAGES.get(expected_phase)
    ctx = _context(job)
    db.rollback()
    if stage_info is None or not _stage_intent(db, job_id, token, expected_phase, stage_info[0]):
        return job_id
    stage = stage_info[0]

    def call(alive):
        if not alive():
            return PublishOutcome(kind="unknown", error_code="lease_lost")
        with SessionLocal() as verify:
            connection = verify.get(PlatformConnection, connection_id) if connection_id else None
            try:
                credential = resolve_credential(connection, owner_id=owner_id, target_open_id=ctx.target_open_id or "")
            except (AttributeError, CredentialError) as exc:
                code = exc.code if isinstance(exc, CredentialError) else "credential_missing"
                return PublishOutcome(kind="not_ready", error_code=code, error_message="发布凭证或账号绑定无效，没有向平台发送；已有产物保留")
            connection_version = connection.credential_version
            reference = connection.credential_ref

        def authorized() -> bool:
            with SessionLocal() as check:
                current = check.get(PlatformConnection, connection_id)
                if not (current is not None and current.owner_id == owner_id and current.status == "connected"
                        and current.purpose == "publish" and current.platform == "douyin"
                        and current.external_account_id == ctx.target_open_id
                        and current.credential_version == connection_version and current.credential_ref == reference):
                    return False
                try:
                    latest = resolve_credential(current, owner_id=owner_id, target_open_id=ctx.target_open_id or "")
                except CredentialError:
                    return False
                return latest.access_token == credential.access_token and latest.client_key == credential.client_key

        return adapter.execute_stage(stage, replace(ctx, ensure_claim=alive, ensure_authorization=authorized), credential)

    outcome = _external_with_lease(job_id, token, call)
    if outcome is not None:
        _stage_outcome(db, job_id, token, stage, ctx, outcome)
    return job_id


def run_due(db: Session, *, adapter_override=None, allow_unready: bool = False) -> int | None:
    if allow_unready and (adapter_override is None or (
        isinstance(adapter_override, DouyinPublishAdapter) and not adapter_override._isolated
    )):
        return None
    claimed = claim_due(db, allow_unready=allow_unready)
    if claimed is None:
        return None
    job, token = claimed
    job_id = job.id
    adapter = adapter_override or get_adapter(job.platform)
    if isinstance(adapter, DouyinPublishAdapter):
        return _run_douyin_stage(db, job, token, adapter)
    ctx = _context(job)
    db.rollback()  # claim_due 的 refresh 重新打开了事务；出网前必须结束。

    def submit(alive):
        guarded = replace(ctx, ensure_claim=alive)
        if not alive():
            return PublishOutcome(kind="unknown", error_code="lease_lost")
        if ctx.provider_request_id:
            outcome = adapter.query_status(guarded)
            if not adapter.catalog().get("query_reliable"):
                outcome = PublishOutcome(
                    kind="unknown",
                    provider_request_id=ctx.provider_request_id,
                    error_code="publish_unknown",
                    error_message="已有上游任务号，但无法可靠查询，不会自动重新提交",
                )
        else:
            outcome = adapter.submit(guarded)
        return outcome

    outcome = _external_with_lease(job_id, token, submit)
    if outcome is not None:
        apply_outcome(db, job_id, token, outcome)
    return job_id


def reconcile_expired(db: Session, *, adapter_override=None) -> int | None:
    """租约过期的 submitting 不重新提交。能可靠查询才对账，否则 publish_unknown。"""
    db.execute(text("SET LOCAL lock_timeout = '8s'"))
    job_id = db.scalar(
        select(PublishJob.id)
        .where(
            PublishJob.status == "submitting",
            PublishJob.lease_until.is_not(None),
            PublishJob.lease_until < func.clock_timestamp(),
        )
        .order_by(PublishJob.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job_id is None:
        db.rollback()
        return None
    job = db.get(PublishJob, job_id, populate_existing=True)
    if job is None:
        db.rollback()
        return None
    adapter = adapter_override or get_adapter(job.platform)
    if isinstance(adapter, DouyinPublishAdapter) and job.phase in {"pending_cover", "cover_uploaded", "video_uploaded"}:
        job.claim_token = None
        job.lease_until = None
        job.error_code = "stage_resume"
        job.error_message = "上一阶段意图尚未发出；保留原快照，等待安全续领"
        db.commit()
        return job.id
    if isinstance(adapter, DouyinPublishAdapter) and job.phase in {"cover_intent", "video_intent", "create_intent"}:
        stage = job.phase.removesuffix("_intent")
        job.status = "publish_unknown"
        job.error_code = f"{stage}_unknown"
        job.error_message = f"{stage} 请求已发出但没有确认结果，不会自动重发"
        job.claim_token = None
        job.lease_until = None
        db.commit()
        return job.id
    reliable = bool(job.provider_request_id) and bool(adapter.catalog().get("query_reliable"))
    if not reliable:
        job.status = "publish_unknown"
        job.error_code = "publish_unknown"
        job.error_message = "提交结果不明，无法可靠查询，不会自动重新提交"
        job.claim_token = None
        job.lease_until = None
        db.commit()
        return job.id
    token = secrets.token_hex(16)
    job.claim_token = token
    job.lease_until = db.scalar(select(func.clock_timestamp())) + LEASE
    ctx = _context(job)
    db.commit()
    outcome = _external_with_lease(
        job_id, token,
        lambda alive: adapter.query_status(replace(ctx, ensure_claim=alive)) if alive() else PublishOutcome(kind="unknown", error_code="lease_lost"),
    )
    if outcome is not None:
        apply_outcome(db, job_id, token, outcome)
    return job_id


def job_public(row: PublishJob) -> dict:
    maybe = row.status in MAYBE_SUBMITTED_STATUSES
    next_action = {
        "cover_uploaded": "审核封面已上传，下一步上传同一快照的视频",
        "video_uploaded": "审核视频已上传，下一步用已确认的封面和视频创建内容",
        "done": "创建请求已接受；平台可能仍在审核，请在平台后台确认公开状态",
    }.get(row.phase)
    if row.status == "publish_unknown":
        next_action = "结果不明，已停止自动重发；请人工核对平台后台"
    elif row.status == "needs_reconfirm":
        next_action = "补齐账号或凭证后重新确认排期"
    elif row.status == "submitting" and row.claim_token is None and row.phase in _STAGES:
        next_action = "修复原账号授权后续跑同一任务，已有产物不会重传"
    elif row.error_code in {"auth_expired", "wrong_scope", "wrong_account", "wrong_owner"}:
        next_action = "检查账号绑定和授权；不要盲目重发，先核对平台后台"
    return {
        "id": row.id,
        "campaign_id": row.campaign_id,
        "owner_id": row.owner_id,
        "platform": row.platform,
        "connection_id": row.connection_id,
        "variant_id": row.variant_id,
        "version": row.version,
        "review_id": row.review_id,
        "scheduled_at": row.scheduled_at,
        "status": row.status,
        "phase": row.phase,
        "next_action": next_action,
        "readiness": row.readiness,
        "copy_snapshot": dict(row.copy_snapshot or {}),
        "asset_order": list(row.asset_order or []),
        "missing": list(row.missing_requirements or []),
        "error_code": row.error_code,
        "error_message": row.error_message,
        "provider_request_id": row.provider_request_id,
        "provider_video_id": row.provider_video_id,
        "cover_image_id": row.cover_image_id,
        "video_upload_id": row.video_upload_id,
        "content_item_id": row.content_item_id,
        "content_video_id": row.content_video_id,
        "cover_log_id": row.cover_log_id,
        "video_log_id": row.video_log_id,
        "create_log_id": row.create_log_id,
        "maybe_submitted": maybe,
        "notice": MAYBE_SUBMITTED_NOTICE if maybe else None,
        "cancelable": row.status in PRE_SUBMIT_STATUSES,
        "publish_url": None,
    }
