"""Server-side Douyin OAuth state and encrypted publish credentials.

Revision ID: 0012_publish_oauth
Revises: 0011_publish_http_stages
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_publish_oauth"
down_revision: Union[str, None] = "0011_publish_http_stages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("platform_connections", sa.Column("app_client_key", sa.String(128), nullable=False, server_default=""))
    op.add_column("platform_connections", sa.Column("credential_origin", sa.String(16), nullable=False, server_default="deployment"))
    op.add_column("platform_connections", sa.Column("credential_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("platform_connections", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("uq_platform_connections_owner_purpose_ext", "platform_connections", type_="unique")
    op.create_unique_constraint("uq_platform_connections_owner_app_purpose_ext", "platform_connections",
                                ["owner_id", "platform", "purpose", "app_client_key", "external_account_id"])

    op.create_table(
        "publish_oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("client_key", sa.String(128), nullable=False),
        sa.Column("callback_uri", sa.String(1024), nullable=False),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("platform_connections.id"), nullable=True),
        sa.Column("connection_version", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "publish_oauth_secrets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("platform_connections.id"), nullable=False, unique=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("client_key", sa.String(128), nullable=False),
        sa.Column("open_id", sa.String(128), nullable=False),
        sa.Column("key_version", sa.String(32), nullable=False),
        sa.Column("access_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("refresh_claim_token", sa.String(64), nullable=True),
        sa.Column("refresh_claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("publish_oauth_secrets")
    op.drop_table("publish_oauth_states")
    op.drop_constraint("uq_platform_connections_owner_app_purpose_ext", "platform_connections", type_="unique")
    op.create_unique_constraint("uq_platform_connections_owner_purpose_ext", "platform_connections",
                                ["owner_id", "platform", "purpose", "external_account_id"])
    op.drop_column("platform_connections", "updated_at")
    op.drop_column("platform_connections", "credential_version")
    op.drop_column("platform_connections", "credential_origin")
    op.drop_column("platform_connections", "app_client_key")
