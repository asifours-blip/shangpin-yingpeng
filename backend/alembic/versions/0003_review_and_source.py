"""generation_tasks：审核字段 / Prompt 来源 / worker 心跳

Revision ID: 0003_review_and_source
Revises: 0002_prompt_ops
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_review_and_source"
down_revision: Union[str, None] = "0002_prompt_ops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generation_tasks",
        sa.Column(
            "review_status",
            sa.String(32),
            nullable=False,
            server_default="unreviewed",
        ),
    )
    op.add_column(
        "generation_tasks",
        sa.Column("review_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "generation_tasks",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "generation_tasks",
        sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column(
        "generation_tasks",
        sa.Column(
            "reverse_op_id",
            sa.Integer(),
            sa.ForeignKey("prompt_operations.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "generation_tasks",
        sa.Column(
            "optimize_op_id",
            sa.Integer(),
            sa.ForeignKey("prompt_operations.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "generation_tasks",
        sa.Column("worker_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generation_tasks", "worker_heartbeat_at")
    op.drop_column("generation_tasks", "optimize_op_id")
    op.drop_column("generation_tasks", "reverse_op_id")
    op.drop_column("generation_tasks", "reviewed_by_id")
    op.drop_column("generation_tasks", "reviewed_at")
    op.drop_column("generation_tasks", "review_reason")
    op.drop_column("generation_tasks", "review_status")
