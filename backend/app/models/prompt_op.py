"""Prompt 反推 / 优化操作记录。与 generation_tasks 分开，同步接口即时落库。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class PromptOperation(Base):
    __tablename__ = "prompt_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    # reverse | optimize
    op_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # optimize 的原文；reverse 可空
    input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # reverse 的参考图；optimize 可空
    input_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("image_assets.id", ondelete="SET NULL"), nullable=True
    )
    # 反推当时的 MinIO object key，资产删了也能对上
    input_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    output_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # succeeded | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="succeeded", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user = relationship("User", back_populates="prompt_operations")
    asset = relationship("ImageAsset", foreign_keys=[input_asset_id])
