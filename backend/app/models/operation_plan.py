"""Scheduled operations rounds; campaigns and existing workers do the actual work."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


RUN_STATUSES = (
    "collecting", "awaiting_selection", "pending_connection", "budget_blocked",
    "paused", "needs_info", "source_empty", "interrupted", "created", "missed", "failed",
)


class OperationPlan(Base):
    __tablename__ = "operation_plans"
    __table_args__ = (
        Index("ix_operation_plans_owner_id", "owner_id"),
        Index("ix_operation_plans_due", "enabled", "next_due_at"),
        CheckConstraint("daily_campaign_limit BETWEEN 1 AND 100", name="ck_operation_plans_daily_campaign_limit"),
        CheckConstraint("daily_budget_limit BETWEEN 0 AND 10000", name="ck_operation_plans_daily_budget_limit"),
        CheckConstraint("generation_budget BETWEEN 0 AND 100", name="ck_operation_plans_generation_budget"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    local_time: Mapped[str] = mapped_column(String(5), nullable=False)
    source_config_id: Mapped[int | None] = mapped_column(ForeignKey("collection_configs.id"), nullable=True)
    product_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    target_platforms: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    daily_campaign_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_budget_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    generation_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_advance_to_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OperationPlanRun(Base):
    __tablename__ = "operation_plan_runs"
    __table_args__ = (
        UniqueConstraint("plan_id", "scheduled_for", name="uq_operation_plan_runs_window"),
        CheckConstraint("status IN (" + ",".join(repr(item) for item in RUN_STATUSES) + ")", name="ck_operation_plan_runs_status"),
        Index("ix_operation_plan_runs_plan_id", "plan_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("operation_plans.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("collection_runs.id"), nullable=True)
    actual_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_items: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    campaign_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    blocker_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blocker_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    missed_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OperationPlanDailyUsage(Base):
    __tablename__ = "operation_plan_daily_usage"
    __table_args__ = (
        UniqueConstraint("plan_id", "local_date", name="uq_operation_plan_daily_usage_day"),
        CheckConstraint("campaign_count >= 0 AND budget_reserved >= 0", name="ck_operation_plan_daily_usage_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("operation_plans.id"), nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    campaign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    budget_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OperationPlanCampaign(Base):
    __tablename__ = "operation_plan_campaigns"
    __table_args__ = (
        UniqueConstraint("campaign_id", name="uq_operation_plan_campaigns_campaign"),
        UniqueConstraint("plan_run_id", "product_id", "source_item_id", name="uq_operation_plan_campaigns_choice"),
        Index("uq_operation_plan_campaigns_no_source", "plan_run_id", "product_id", unique=True,
              postgresql_where=text("source_item_id IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_run_id: Mapped[int] = mapped_column(ForeignKey("operation_plan_runs.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("owned_products.id"), nullable=False)
    source_item_id: Mapped[int | None] = mapped_column(ForeignKey("source_items.id"), nullable=True)
