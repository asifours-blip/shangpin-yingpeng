"""Fence stale OAuth callbacks by database-assigned authorization attempt order.

Revision ID: 0013_publish_oauth_attempt_order
Revises: 0012_publish_oauth
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_publish_oauth_attempt_order"
down_revision: Union[str, None] = "0012_publish_oauth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 旧 state 没有可与新尝试比较的序号，必须重新发起授权。
    op.execute("UPDATE publish_oauth_states SET consumed_at = clock_timestamp() WHERE consumed_at IS NULL")
    op.add_column("publish_oauth_states", sa.Column(
        "attempt_id", sa.BigInteger(), sa.Identity(always=False), nullable=False,
    ))
    op.create_unique_constraint("uq_publish_oauth_states_attempt_id", "publish_oauth_states", ["attempt_id"])
    op.add_column("platform_connections", sa.Column(
        "last_authorization_attempt_id", sa.BigInteger(), nullable=False, server_default="0",
    ))


def downgrade() -> None:
    op.drop_column("platform_connections", "last_authorization_attempt_id")
    op.drop_constraint("uq_publish_oauth_states_attempt_id", "publish_oauth_states", type_="unique")
    op.drop_column("publish_oauth_states", "attempt_id")
