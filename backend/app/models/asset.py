"""图片资产与任务关联。数据库只存对象 Key，不存会过期的签名 URL。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class ImageAsset(Base):
    __tablename__ = "image_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    mime: Mapped[str] = mapped_column(String(64), nullable=False, default="image/png")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner = relationship("User", back_populates="assets")
    task_links = relationship("GenerationTaskAsset", back_populates="asset")


class GenerationTaskAsset(Base):
    __tablename__ = "generation_task_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("generation_tasks.id"), nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("image_assets.id"), nullable=False, index=True)
    # product / scene / input（旧）/ output
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    task = relationship("GenerationTask", back_populates="assets")
    asset = relationship("ImageAsset", back_populates="task_links")
