"""prompt_operations：反推 / Prompt 优化落库

Revision ID: 0002_prompt_ops
Revises: 0001_init
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_prompt_ops"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("op_type", sa.String(16), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=True),
        sa.Column(
            "input_asset_id",
            sa.Integer(),
            sa.ForeignKey("image_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("input_object_key", sa.String(512), nullable=True),
        sa.Column("output_prompt", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="succeeded"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_prompt_operations_user_id", "prompt_operations", ["user_id"])
    op.create_index("ix_prompt_operations_status", "prompt_operations", ["status"])
    op.create_index(
        "ix_prompt_operations_user_created",
        "prompt_operations",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_prompt_operations_user_created", table_name="prompt_operations")
    op.drop_index("ix_prompt_operations_status", table_name="prompt_operations")
    op.drop_index("ix_prompt_operations_user_id", table_name="prompt_operations")
    op.drop_table("prompt_operations")
