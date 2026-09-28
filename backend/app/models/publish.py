"""发布任务。绑定已审核快照，不跟随后续改稿。"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ACTIVE_STATUSES = (
    "scheduled",
    "needs_reconfirm",
    "submitting",
    "uploaded",
    "create_accepted",
    "publish_unknown",
)
PRE_SUBMIT_STATUSES = ("scheduled", "needs_reconfirm")
# 进入这些状态后，界面必须说明可能已经提交，不能宣称撤回了平台内容。
MAYBE_SUBMITTED_STATUSES = ("submitting", "uploaded", "create_accepted", "publish_unknown", "published")


class PublishJob(Base):
    __tablename__ = "publish_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled','needs_reconfirm','submitting','uploaded','create_accepted',"
            "'publish_unknown','failed','cancelled','invalidated','published')",
            name="ck_publish_jobs_status",
        ),
        CheckConstraint(
            "readiness IN ('ready','pending_connection','approved_ready_to_publish')",
            name="ck_publish_jobs_readiness",
        ),
        Index("ix_publish_jobs_owner_id", "owner_id"),
        Index("ix_publish_jobs_campaign_id", "campaign_id"),
        Index("ix_publish_jobs_status_scheduled", "status", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_connections.id"), nullable=True
    )
    variant_id: Mapped[int] = mapped_column(ForeignKey("content_variants.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    review_id: Mapped[int] = mapped_column(ForeignKey("variant_reviews.id"), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    copy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    asset_order: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    missing_requirements: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    readiness: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    local_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_video_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_cover")
    target_open_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cover_image_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    video_upload_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content_item_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    content_video_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cover_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    video_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cover_intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cover_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    video_intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    video_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    create_intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    create_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cover_log_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    video_log_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    create_log_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
