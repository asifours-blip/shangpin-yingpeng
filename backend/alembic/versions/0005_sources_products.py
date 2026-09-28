"""来源采集与自家商品事实版本。

Revision ID: 0005_sources_products
Revises: 0004_demo_ops
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_sources_products"
down_revision: Union[str, None] = "0004_demo_ops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("external_account_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("scope_set", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credential_ref", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("purpose IN ('data', 'publish')", name="ck_platform_connections_purpose"),
        sa.CheckConstraint(
            "status IN ('pending', 'connected', 'expired', 'revoked')",
            name="ck_platform_connections_status",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "platform",
            "purpose",
            "external_account_id",
            name="uq_platform_connections_owner_purpose_ext",
        ),
    )
    op.create_index("ix_platform_connections_owner_id", "platform_connections", ["owner_id"])

    op.create_table(
        "collection_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("item_kind", sa.String(16), nullable=False),
        sa.Column("category", sa.String(64), nullable=False, server_default=""),
        sa.Column("query", sa.String(256), nullable=False, server_default=""),
        sa.Column("window", sa.String(32), nullable=False, server_default="7d"),
        sa.Column("sort_metric", sa.String(64), nullable=False, server_default="total_sales"),
        sa.Column("max_items", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("schedule", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "provider_settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("item_kind IN ('product', 'post')", name="ck_collection_configs_item_kind"),
        sa.CheckConstraint(
            "max_items >= 1 AND max_items <= 100",
            name="ck_collection_configs_max_items",
        ),
    )
    op.create_index("ix_collection_configs_owner_id", "collection_configs", ["owner_id"])

    op.create_table(
        "collection_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("config_id", sa.Integer(), sa.ForeignKey("collection_configs.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("actual_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cursor_checkpoint", sa.String(256), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("provider_response_version", sa.String(128), nullable=True),
        sa.Column("scope_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("dropped_duplicate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dropped_invalid", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'partial', 'failed')",
            name="ck_collection_runs_status",
        ),
    )
    op.create_index("ix_collection_runs_config_id", "collection_runs", ["config_id"])

    op.create_table(
        "source_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("collection_runs.id"), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("item_kind", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("text_excerpt", sa.Text(), nullable=True),
        sa.Column("media_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("raw_metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_rank", sa.Integer(), nullable=True),
        sa.Column("raw_payload_key", sa.String(512), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
        sa.Column("rights_scope", sa.String(64), nullable=False, server_default="provider_terms"),
        sa.CheckConstraint("item_kind IN ('product', 'post')", name="ck_source_items_item_kind"),
        sa.UniqueConstraint(
            "run_id",
            "platform",
            "item_kind",
            "external_id",
            name="uq_source_items_run_platform_kind_ext",
        ),
    )
    op.create_index("ix_source_items_run_id", "source_items", ["run_id"])

    op.create_table(
        "owned_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("primary_asset_id", sa.Integer(), sa.ForeignKey("image_assets.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "sku", name="uq_owned_products_owner_sku"),
    )
    op.create_index("ix_owned_products_owner_id", "owned_products", ["owner_id"])

    op.create_table(
        "product_fact_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("owned_products.id"), nullable=False),
        sa.Column("facts", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "claim_evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("product_id", "version", name="uq_product_fact_versions_product_version"),
    )
    op.create_index("ix_product_fact_versions_product_id", "product_fact_versions", ["product_id"])


def downgrade() -> None:
    op.drop_table("product_fact_versions")
    op.drop_table("owned_products")
    op.drop_table("source_items")
    op.drop_table("collection_runs")
    op.drop_table("collection_configs")
    op.drop_table("platform_connections")
