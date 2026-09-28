"""演示运营：冻结/申诉/通知/社交/文案/软删列

Revision ID: 0004_demo_ops
Revises: 0003_review_and_source
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_demo_ops"
down_revision: Union[str, None] = "0003_review_and_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "freeze_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("frozen_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "frozen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("unfrozen_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("unfrozen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_freeze_events_user_id", "freeze_events", ["user_id"])
    op.create_index("ix_freeze_events_user_frozen_at", "freeze_events", ["user_id", "frozen_at"])

    op.add_column(
        "users",
        sa.Column("is_frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("current_freeze_event_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_current_freeze_event_id",
        "users",
        "freeze_events",
        ["current_freeze_event_id"],
        ["id"],
    )

    op.create_table(
        "appeals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "freeze_event_id",
            sa.Integer(),
            sa.ForeignKey("freeze_events.id"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("admin_reply", sa.Text(), nullable=True),
        sa.Column("handled_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('pending', 'closed')", name="ck_appeals_status"),
    )
    op.create_index("ix_appeals_user_id", "appeals", ["user_id"])
    op.create_index("ix_appeals_freeze_event_id", "appeals", ["freeze_event_id"])
    op.create_index("ix_appeals_status", "appeals", ["status"])
    op.create_index(
        "uq_appeals_event_pending",
        "appeals",
        ["freeze_event_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.add_column(
        "generation_tasks",
        sa.Column("user_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "generation_tasks",
        sa.Column("user_deleted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index(
        "ix_generation_tasks_user_deleted_at",
        "generation_tasks",
        ["user_id", "user_deleted_at"],
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("generation_tasks.id"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'handled')", name="ck_notifications_status"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_task_id", "notifications", ["task_id"])
    op.create_index("ix_notifications_user_status", "notifications", ["user_id", "status"])
    op.create_index(
        "uq_notifications_user_task_pending",
        "notifications",
        ["user_id", "task_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "social_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("external_account_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="disconnected"),
        sa.Column("data_source", sa.String(32), nullable=False, server_default="sample"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('connected', 'disconnected')",
            name="ck_social_accounts_status",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "platform",
            "external_account_id",
            name="uq_social_accounts_owner_platform_ext",
        ),
    )
    op.create_index("ix_social_accounts_owner_id", "social_accounts", ["owner_id"])
    op.create_index(
        "ix_social_accounts_owner_status",
        "social_accounts",
        ["owner_id", "status"],
    )

    op.create_table(
        "social_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("social_accounts.id"),
            nullable=False,
        ),
        sa.Column("external_user_id", sa.String(64), nullable=False),
        sa.Column("nickname", sa.String(128), nullable=False),
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("remark", sa.String(256), nullable=True),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("data_source", sa.String(32), nullable=False, server_default="sample"),
        sa.UniqueConstraint(
            "account_id",
            "external_user_id",
            name="uq_social_contacts_account_ext",
        ),
    )
    op.create_index("ix_social_contacts_account_id", "social_contacts", ["account_id"])

    op.create_table(
        "copywriting_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "input_asset_id",
            sa.Integer(),
            sa.ForeignKey("image_assets.id"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column(
            "product_facts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("generated_content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("edited_content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("risk_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="processing"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("user_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_deleted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'succeeded', 'failed')",
            name="ck_copywriting_status",
        ),
        sa.CheckConstraint(
            "platform IN ('douyin', 'xiaohongshu')",
            name="ck_copywriting_platform",
        ),
    )
    op.create_index("ix_copywriting_user_id", "copywriting_operations", ["user_id"])
    op.create_index(
        "ix_copywriting_user_created",
        "copywriting_operations",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_copywriting_user_deleted_at",
        "copywriting_operations",
        ["user_id", "user_deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_copywriting_user_deleted_at", table_name="copywriting_operations")
    op.drop_index("ix_copywriting_user_created", table_name="copywriting_operations")
    op.drop_index("ix_copywriting_user_id", table_name="copywriting_operations")
    op.drop_table("copywriting_operations")

    op.drop_index("ix_social_contacts_account_id", table_name="social_contacts")
    op.drop_table("social_contacts")

    op.drop_index("ix_social_accounts_owner_status", table_name="social_accounts")
    op.drop_index("ix_social_accounts_owner_id", table_name="social_accounts")
    op.drop_table("social_accounts")

    op.drop_index("uq_notifications_user_task_pending", table_name="notifications")
    op.drop_index("ix_notifications_user_status", table_name="notifications")
    op.drop_index("ix_notifications_task_id", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_generation_tasks_user_deleted_at", table_name="generation_tasks")
    op.drop_column("generation_tasks", "user_deleted_by")
    op.drop_column("generation_tasks", "user_deleted_at")

    op.drop_index("uq_appeals_event_pending", table_name="appeals")
    op.drop_index("ix_appeals_status", table_name="appeals")
    op.drop_index("ix_appeals_freeze_event_id", table_name="appeals")
    op.drop_index("ix_appeals_user_id", table_name="appeals")
    op.drop_table("appeals")

    op.drop_constraint("fk_users_current_freeze_event_id", "users", type_="foreignkey")
    op.drop_column("users", "current_freeze_event_id")
    op.drop_column("users", "is_frozen")

    op.drop_index("ix_freeze_events_user_frozen_at", table_name="freeze_events")
    op.drop_index("ix_freeze_events_user_id", table_name="freeze_events")
    op.drop_table("freeze_events")
