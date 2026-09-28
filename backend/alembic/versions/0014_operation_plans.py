"""Scheduled operations plans, unique windows, daily reservations and campaign links.

Revision ID: 0014_operation_plans
Revises: 0013_publish_oauth_attempt_order
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_operation_plans"
down_revision: Union[str, None] = "0013_publish_oauth_attempt_order"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operation_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("local_time", sa.String(5), nullable=False),
        sa.Column("source_config_id", sa.Integer(), sa.ForeignKey("collection_configs.id")),
        sa.Column("product_ids", postgresql.JSONB(), nullable=False),
        sa.Column("target_platforms", postgresql.JSONB(), nullable=False),
        sa.Column("daily_campaign_limit", sa.Integer(), nullable=False),
        sa.Column("daily_budget_limit", sa.Integer(), nullable=False),
        sa.Column("generation_budget", sa.Integer(), nullable=False),
        sa.Column("auto_advance_to_review", sa.Boolean(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("daily_campaign_limit BETWEEN 1 AND 100", name="ck_operation_plans_daily_campaign_limit"),
        sa.CheckConstraint("daily_budget_limit BETWEEN 0 AND 10000", name="ck_operation_plans_daily_budget_limit"),
        sa.CheckConstraint("generation_budget BETWEEN 0 AND 100", name="ck_operation_plans_generation_budget"),
    )
    op.create_index("ix_operation_plans_owner_id", "operation_plans", ["owner_id"])
    op.create_index("ix_operation_plans_due", "operation_plans", ["enabled", "next_due_at"])
    op.create_table(
        "operation_plan_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("operation_plans.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("collection_runs.id")),
        sa.Column("actual_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_items", postgresql.JSONB(), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("campaign_ids", postgresql.JSONB(), nullable=False),
        sa.Column("blocker_code", sa.String(64)),
        sa.Column("blocker_message", sa.Text()),
        sa.Column("missed_from", sa.DateTime(timezone=True)),
        sa.Column("missed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('collecting','awaiting_selection','pending_connection','budget_blocked','paused','needs_info','source_empty','interrupted','created','missed','failed')",
            name="ck_operation_plan_runs_status",
        ),
        sa.UniqueConstraint("plan_id", "scheduled_for", name="uq_operation_plan_runs_window"),
    )
    op.create_index("ix_operation_plan_runs_plan_id", "operation_plan_runs", ["plan_id"])
    op.create_table(
        "operation_plan_daily_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("operation_plans.id"), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("campaign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("campaign_count >= 0 AND budget_reserved >= 0", name="ck_operation_plan_daily_usage_nonnegative"),
        sa.UniqueConstraint("plan_id", "local_date", name="uq_operation_plan_daily_usage_day"),
    )
    op.create_table(
        "operation_plan_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), sa.ForeignKey("operation_plan_runs.id"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("owned_products.id"), nullable=False),
        sa.Column("source_item_id", sa.Integer(), sa.ForeignKey("source_items.id")),
        sa.UniqueConstraint("campaign_id", name="uq_operation_plan_campaigns_campaign"),
        sa.UniqueConstraint("plan_run_id", "product_id", "source_item_id", name="uq_operation_plan_campaigns_choice"),
    )
    op.create_index("uq_operation_plan_campaigns_no_source", "operation_plan_campaigns", ["plan_run_id", "product_id"],
                    unique=True, postgresql_where=sa.text("source_item_id IS NULL"))


def downgrade() -> None:
    op.drop_index("uq_operation_plan_campaigns_no_source", table_name="operation_plan_campaigns")
    op.drop_table("operation_plan_campaigns")
    op.drop_table("operation_plan_daily_usage")
    op.drop_index("ix_operation_plan_runs_plan_id", table_name="operation_plan_runs")
    op.drop_table("operation_plan_runs")
    op.drop_index("ix_operation_plans_due", table_name="operation_plans")
    op.drop_index("ix_operation_plans_owner_id", table_name="operation_plans")
    op.drop_table("operation_plans")
