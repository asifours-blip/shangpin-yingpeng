"""活动、流水线步骤和内容变体。

Revision ID: 0006_campaign_pipeline
Revises: 0005_sources_products
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_campaign_pipeline"
down_revision: Union[str, None] = "0005_sources_products"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CAMPAIGN_STATUS = (
    "status IN ('draft','collecting','planning','generating','needs_review',"
    "'approved','scheduled','publishing','platform_review','published',"
    "'blocked','failed','publish_unknown','platform_rejected')"
)


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("owned_products.id"), nullable=False),
        sa.Column(
            "fact_version_id",
            sa.Integer(),
            sa.ForeignKey("product_fact_versions.id"),
            nullable=False,
        ),
        sa.Column("brief", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "selected_source_item_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_CAMPAIGN_STATUS, name="ck_campaigns_status"),
    )
    op.create_index("ix_campaigns_owner_id", "campaigns", ["owner_id"])

    op.create_table(
        "campaign_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("recipe_version", sa.String(32), nullable=False, server_default="bags-v1"),
        sa.Column("generation_budget", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_campaign_runs_campaign_id", "campaign_runs", ["campaign_id"])

    op.create_table(
        "pipeline_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("campaign_runs.id"), nullable=False),
        sa.Column("step_key", sa.String(64), nullable=False),
        sa.Column("variant_platform", sa.String(32), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("local_request_id", sa.String(64), nullable=True),
        sa.Column("provider_request_id", sa.String(128), nullable=True),
        sa.Column("generation_task_id", sa.Integer(), sa.ForeignKey("generation_tasks.id"), nullable=True),
        sa.Column(
            "copywriting_operation_id",
            sa.Integer(),
            sa.ForeignKey("copywriting_operations.id"),
            nullable=True,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("output", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'unknown', 'skipped')",
            name="ck_pipeline_steps_status",
        ),
        sa.UniqueConstraint(
            "run_id",
            "step_key",
            "variant_platform",
            "version",
            name="uq_pipeline_steps_run_key_platform_version",
        ),
    )
    op.create_index("ix_pipeline_steps_run_id", "pipeline_steps", ["run_id"])
    op.create_index("ix_pipeline_steps_status", "pipeline_steps", ["status"])

    op.create_table(
        "content_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("hashtags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("storyboard", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("qc_result", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint(
            "campaign_id",
            "platform",
            "content_type",
            "version",
            name="uq_content_variants_campaign_platform_type_version",
        ),
    )
    op.create_index("ix_content_variants_campaign_id", "content_variants", ["campaign_id"])

    op.create_table(
        "variant_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("content_variants.id"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("image_assets.id"), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_variant_assets_variant_id", "variant_assets", ["variant_id"])


def downgrade() -> None:
    op.drop_table("variant_assets")
    op.drop_table("content_variants")
    op.drop_table("pipeline_steps")
    op.drop_table("campaign_runs")
    op.drop_table("campaigns")
