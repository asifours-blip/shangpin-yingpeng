"""init users / generation_tasks / image_assets / generation_task_assets

Revision ID: 0001_init
Revises:
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )

    op.create_table(
        "generation_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False, server_default="t2i"),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_of_id", sa.Integer(), sa.ForeignKey("generation_tasks.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_generation_tasks_user_id", "generation_tasks", ["user_id"])
    op.create_index("ix_generation_tasks_status", "generation_tasks", ["status"])
    op.create_index("ix_generation_tasks_user_created", "generation_tasks", ["user_id", "created_at"])

    op.create_table(
        "image_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("bucket", sa.String(128), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("mime", sa.String(64), nullable=False, server_default="image/png"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("object_key", name="uq_image_assets_object_key"),
    )
    op.create_index("ix_image_assets_owner_id", "image_assets", ["owner_id"])

    op.create_table(
        "generation_task_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("generation_tasks.id"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("image_assets.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_generation_task_assets_task_id", "generation_task_assets", ["task_id"])
    op.create_index("ix_generation_task_assets_asset_id", "generation_task_assets", ["asset_id"])


def downgrade() -> None:
    op.drop_table("generation_task_assets")
    op.drop_table("image_assets")
    op.drop_table("generation_tasks")
    op.drop_table("users")
