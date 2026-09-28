"""活动启动幂等键、步骤依赖和心跳。

Revision ID: 0007_campaign_runtime
Revises: 0006_campaign_pipeline
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_campaign_runtime"
down_revision: Union[str, None] = "0006_campaign_pipeline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "campaign_runs",
        sa.Column("idempotency_key", sa.String(128), nullable=True),
    )
    op.create_index(
        "uq_campaign_runs_campaign_idempotency",
        "campaign_runs",
        ["campaign_id", "idempotency_key"],
        unique=True,
    )
    op.add_column(
        "pipeline_steps",
        sa.Column(
            "depends_on",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "pipeline_steps",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipeline_steps", "heartbeat_at")
    op.drop_column("pipeline_steps", "depends_on")
    op.drop_index("uq_campaign_runs_campaign_idempotency", table_name="campaign_runs")
    op.drop_column("campaign_runs", "idempotency_key")
