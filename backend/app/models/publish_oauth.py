"""抖音发布授权的一次性状态与服务器端密文。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PublishOAuthState(Base):
    __tablename__ = "publish_oauth_states"
    __table_args__ = (UniqueConstraint("attempt_id", name="uq_publish_oauth_states_attempt_id"),)

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    client_key: Mapped[str] = mapped_column(String(128), nullable=False)
    callback_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("platform_connections.id"), nullable=True)
    connection_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PublishOAuthSecret(Base):
    __tablename__ = "publish_oauth_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("platform_connections.id"), unique=True, nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    client_key: Mapped[str] = mapped_column(String(128), nullable=False)
    open_id: Mapped[str] = mapped_column(String(128), nullable=False)
    key_version: Mapped[str] = mapped_column(String(32), nullable=False)
    access_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    refresh_claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refresh_claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
