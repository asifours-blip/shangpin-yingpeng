"""领取令牌和预算预占计数。旧活动行保持可读。

Revision ID: 0008_claim_and_budget
Revises: 0007_campaign_runtime
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_claim_and_budget"
down_revision: Union[str, None] = "0007_campaign_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pipeline_steps", sa.Column("claim_token", sa.String(64), nullable=True))
    op.add_column(
        "campaign_runs",
        sa.Column("budget_reserved", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("campaign_runs", "budget_reserved")
    op.drop_column("pipeline_steps", "claim_token")
