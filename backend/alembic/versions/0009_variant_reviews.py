"""变体审核记录。旧版本和旧事实快照保持可读。

Revision ID: 0009_variant_reviews
Revises: 0008_claim_and_budget
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_variant_reviews"
down_revision: Union[str, None] = "0008_claim_and_budget"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("content_variants", sa.Column("fact_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_content_variants_fact_version_id",
        "content_variants",
        "product_fact_versions",
        ["fact_version_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE content_variants AS variant
        SET fact_version_id = campaign.fact_version_id
        FROM campaigns AS campaign
        WHERE variant.campaign_id = campaign.id
          AND variant.fact_version_id IS NULL
        """
    )
    op.add_column("pipeline_steps", sa.Column("variant_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_pipeline_steps_variant_id",
        "pipeline_steps",
        "content_variants",
        ["variant_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE pipeline_steps AS step
        SET variant_id = variant.id
        FROM campaign_runs AS run
        JOIN content_variants AS variant
          ON variant.campaign_id = run.campaign_id
        WHERE step.run_id = run.id
          AND variant.platform = step.variant_platform
          AND variant.version = step.version
          AND step.variant_platform <> ''
          AND step.variant_id IS NULL
        """
    )
    op.create_table(
        "variant_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("content_variants.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("copy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("asset_order", postgresql.JSONB(), nullable=False),
        sa.Column("fact_version_id", sa.Integer(), sa.ForeignKey("product_fact_versions.id"), nullable=False),
        sa.Column("fact_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("qc_snapshot", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name="ck_variant_reviews_decision",
        ),
    )
    op.create_index("ix_variant_reviews_variant_id", "variant_reviews", ["variant_id"])


def downgrade() -> None:
    op.drop_index("ix_variant_reviews_variant_id", table_name="variant_reviews")
    op.drop_table("variant_reviews")
    op.drop_constraint("fk_pipeline_steps_variant_id", "pipeline_steps", type_="foreignkey")
    op.drop_column("pipeline_steps", "variant_id")
    op.drop_constraint("fk_content_variants_fact_version_id", "content_variants", type_="foreignkey")
    op.drop_column("content_variants", "fact_version_id")
