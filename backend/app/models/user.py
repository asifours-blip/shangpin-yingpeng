"""用户表。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_freeze_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("freeze_events.id", use_alter=True, name="fk_users_current_freeze_event_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tasks = relationship(
        "GenerationTask",
        back_populates="user",
        foreign_keys="GenerationTask.user_id",
    )
    assets = relationship("ImageAsset", back_populates="owner")
    prompt_operations = relationship("PromptOperation", back_populates="user")
