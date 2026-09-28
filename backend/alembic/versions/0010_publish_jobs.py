"""发布任务。快照在排期时冻结，执行时不再拿最新文案。

Revision ID: 0010_publish_jobs
Revises: 0009_variant_reviews
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_publish_jobs"
down_revision: Union[str, None] = "0009_variant_reviews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE = (
    "scheduled",
    "needs_reconfirm",
    "submitting",
    "uploaded",
    "create_accepted",
    "publish_unknown",
)


def upgrade() -> None:
    op.create_table(
        "publish_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("platform_connections.id"), nullable=True),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("content_variants.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("variant_reviews.id"), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("copy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("asset_order", postgresql.JSONB(), nullable=False),
        sa.Column("missing_requirements", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("readiness", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("local_request_id", sa.String(64), nullable=True),
        sa.Column("provider_request_id", sa.String(256), nullable=True),
        sa.Column("provider_video_id", sa.String(256), nullable=True),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('scheduled','needs_reconfirm','submitting','uploaded','create_accepted',"
            "'publish_unknown','failed','cancelled','invalidated','published')",
            name="ck_publish_jobs_status",
        ),
        sa.CheckConstraint(
            "readiness IN ('ready','pending_connection','approved_ready_to_publish')",
            name="ck_publish_jobs_readiness",
        ),
    )
    op.create_index("ix_publish_jobs_owner_id", "publish_jobs", ["owner_id"])
    op.create_index("ix_publish_jobs_campaign_id", "publish_jobs", ["campaign_id"])
    op.create_index("ix_publish_jobs_status_scheduled", "publish_jobs", ["status", "scheduled_at"])
    op.execute(
        """
        CREATE UNIQUE INDEX uq_publish_jobs_owner_idempotency
        ON publish_jobs (owner_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    active = ", ".join(f"'{item}'" for item in _ACTIVE)
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_publish_jobs_active_connection
        ON publish_jobs (connection_id, variant_id, version)
        WHERE connection_id IS NOT NULL AND status IN ({active})
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_publish_jobs_active_unbound
        ON publish_jobs (owner_id, platform, variant_id, version)
        WHERE connection_id IS NULL AND status IN ({active})
        """
    )


def downgrade() -> None:
    op.drop_index("uq_publish_jobs_active_unbound", table_name="publish_jobs")
    op.drop_index("uq_publish_jobs_active_connection", table_name="publish_jobs")
    op.drop_index("uq_publish_jobs_owner_idempotency", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_status_scheduled", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_campaign_id", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_owner_id", table_name="publish_jobs")
    op.drop_table("publish_jobs")
