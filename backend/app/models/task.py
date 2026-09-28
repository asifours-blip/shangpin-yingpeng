"""生成任务。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class GenerationTask(Base):
    __tablename__ = "generation_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="t2i")
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_of_id: Mapped[int | None] = mapped_column(ForeignKey("generation_tasks.id"), nullable=True)
    # 审核：unreviewed（默认）/ usable / needs_revision
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unreviewed")
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Prompt 来源（可选，禁止按时间自动关联）
    reverse_op_id: Mapped[int | None] = mapped_column(ForeignKey("prompt_operations.id"), nullable=True)
    optimize_op_id: Mapped[int | None] = mapped_column(ForeignKey("prompt_operations.id"), nullable=True)
    # Worker 心跳；迟到结果若状态已不是 running 则丢弃
    worker_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="tasks", foreign_keys=[user_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id])
    deleted_by = relationship("User", foreign_keys=[user_deleted_by])
    assets = relationship("GenerationTaskAsset", back_populates="task")
