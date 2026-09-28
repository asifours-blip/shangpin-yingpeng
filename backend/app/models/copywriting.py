"""独立营销文案记录，不改 prompt_operations。"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class CopywritingOperation(Base):
    __tablename__ = "copywriting_operations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'succeeded', 'failed')",
            name="ck_copywriting_status",
        ),
        CheckConstraint(
            "platform IN ('douyin', 'xiaohongshu')",
            name="ck_copywriting_platform",
        ),
        Index("ix_copywriting_user_id", "user_id"),
        Index("ix_copywriting_user_created", "user_id", "created_at"),
        Index("ix_copywriting_user_deleted_at", "user_id", "user_deleted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    input_asset_id: Mapped[int] = mapped_column(ForeignKey("image_assets.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    product_facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    generated_content: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    edited_content: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    risk_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processing")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user = relationship("User", foreign_keys=[user_id])
    input_asset = relationship("ImageAsset", foreign_keys=[input_asset_id])
    deleted_by = relationship("User", foreign_keys=[user_deleted_by])
