"""冻结事件与申诉。"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class FreezeEvent(Base):
    __tablename__ = "freeze_events"
    __table_args__ = (
        Index("ix_freeze_events_user_id", "user_id"),
        Index("ix_freeze_events_user_frozen_at", "user_id", "frozen_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    frozen_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    unfrozen_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    unfrozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    frozen_by = relationship("User", foreign_keys=[frozen_by_id])
    unfrozen_by = relationship("User", foreign_keys=[unfrozen_by_id])
    appeals = relationship("Appeal", back_populates="freeze_event")


class Appeal(Base):
    __tablename__ = "appeals"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'closed')", name="ck_appeals_status"),
        Index("ix_appeals_user_id", "user_id"),
        Index("ix_appeals_freeze_event_id", "freeze_event_id"),
        Index("ix_appeals_status", "status"),
        Index(
            "uq_appeals_event_pending",
            "freeze_event_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    freeze_event_id: Mapped[int] = mapped_column(ForeignKey("freeze_events.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    admin_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    handled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user = relationship("User", foreign_keys=[user_id])
    freeze_event = relationship("FreezeEvent", back_populates="appeals")
    handled_by = relationship("User", foreign_keys=[handled_by_id])
