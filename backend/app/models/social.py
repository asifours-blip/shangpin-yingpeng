"""演示社交账号与联系人。"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('connected', 'disconnected')",
            name="ck_social_accounts_status",
        ),
        UniqueConstraint(
            "owner_id",
            "platform",
            "external_account_id",
            name="uq_social_accounts_owner_platform_ext",
        ),
        Index("ix_social_accounts_owner_id", "owner_id"),
        Index("ix_social_accounts_owner_status", "owner_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="disconnected")
    data_source: Mapped[str] = mapped_column(String(32), nullable=False, default="sample")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner = relationship("User", foreign_keys=[owner_id])
    contacts = relationship("SocialContact", back_populates="account")


class SocialContact(Base):
    __tablename__ = "social_contacts"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "external_user_id",
            name="uq_social_contacts_account_ext",
        ),
        Index("ix_social_contacts_account_id", "account_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id"), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    nickname: Mapped[str] = mapped_column(String(128), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    data_source: Mapped[str] = mapped_column(String(32), nullable=False, default="sample")

    account = relationship("SocialAccount", back_populates="contacts")
