"""活动、步骤、内容变体和审核记录。发布任务见 publish_jobs。"""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

CAMPAIGN_STATUSES = (
    "draft",
    "collecting",
    "planning",
    "generating",
    "needs_review",
    "approved",
    "scheduled",
    "publishing",
    "platform_review",
    "published",
    "blocked",
    "failed",
    "publish_unknown",
    "platform_rejected",
)


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ",".join(repr(item) for item in CAMPAIGN_STATUSES) + ")",
            name="ck_campaigns_status",
        ),
        Index("ix_campaigns_owner_id", "owner_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("owned_products.id"), nullable=False)
    fact_version_id: Mapped[int] = mapped_column(
        ForeignKey("product_fact_versions.id"), nullable=False
    )
    brief: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    selected_source_item_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    runs = relationship("CampaignRun", back_populates="campaign")
    variants = relationship("ContentVariant", back_populates="campaign")


class CampaignRun(Base):
    __tablename__ = "campaign_runs"
    __table_args__ = (
        Index("ix_campaign_runs_campaign_id", "campaign_id"),
        UniqueConstraint(
            "campaign_id",
            "idempotency_key",
            name="uq_campaign_runs_campaign_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    recipe_version: Mapped[str] = mapped_column(String(32), nullable=False, default="bags-v1")
    generation_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    budget_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    campaign = relationship("Campaign", back_populates="runs")
    steps = relationship("PipelineStep", back_populates="run")


class PipelineStep(Base):
    __tablename__ = "pipeline_steps"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "step_key",
            "variant_platform",
            "version",
            name="uq_pipeline_steps_run_key_platform_version",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'unknown', 'skipped')",
            name="ck_pipeline_steps_status",
        ),
        Index("ix_pipeline_steps_run_id", "run_id"),
        Index("ix_pipeline_steps_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("campaign_runs.id"), nullable=False)
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    variant_platform: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    local_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("generation_tasks.id"), nullable=True
    )
    copywriting_operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("copywriting_operations.id"), nullable=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    depends_on: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_variants.id"), nullable=True
    )

    run = relationship("CampaignRun", back_populates="steps")
    variant = relationship("ContentVariant")


class ContentVariant(Base):
    __tablename__ = "content_variants"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "platform",
            "content_type",
            "version",
            name="uq_content_variants_campaign_platform_type_version",
        ),
        Index("ix_content_variants_campaign_id", "campaign_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    storyboard: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    qc_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    fact_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_fact_versions.id"), nullable=True
    )

    campaign = relationship("Campaign", back_populates="variants")
    assets = relationship("VariantAsset", back_populates="variant")
    reviews = relationship("VariantReview", back_populates="variant")


class VariantAsset(Base):
    __tablename__ = "variant_assets"
    __table_args__ = (
        Index("ix_variant_assets_variant_id", "variant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("content_variants.id"), nullable=False)
    asset_id: Mapped[int] = mapped_column(ForeignKey("image_assets.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    variant = relationship("ContentVariant", back_populates="assets")


class VariantReview(Base):
    __tablename__ = "variant_reviews"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_variant_reviews_decision",
        ),
        Index("ix_variant_reviews_variant_id", "variant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("content_variants.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    copy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    asset_order: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    fact_version_id: Mapped[int] = mapped_column(
        ForeignKey("product_fact_versions.id"), nullable=False
    )
    fact_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    qc_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    variant = relationship("ContentVariant", back_populates="reviews")
