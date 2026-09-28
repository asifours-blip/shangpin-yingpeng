"""采集配置、批次与来源样本。不是社交联系人。"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class PlatformConnection(Base):
    __tablename__ = "platform_connections"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "platform", "purpose", "app_client_key", "external_account_id",
            name="uq_platform_connections_owner_app_purpose_ext",
        ),
        CheckConstraint(
            "purpose IN ('data', 'publish')",
            name="ck_platform_connections_purpose",
        ),
        CheckConstraint(
            "status IN ('pending', 'connected', 'expired', 'revoked')",
            name="ck_platform_connections_status",
        ),
        Index("ix_platform_connections_owner_id", "owner_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    scope_set: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    app_client_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    credential_origin: Mapped[str] = mapped_column(String(16), nullable=False, default="deployment")
    credential_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_authorization_attempt_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CollectionConfig(Base):
    __tablename__ = "collection_configs"
    __table_args__ = (
        CheckConstraint(
            "item_kind IN ('product', 'post')",
            name="ck_collection_configs_item_kind",
        ),
        CheckConstraint(
            "max_items >= 1 AND max_items <= 100",
            name="ck_collection_configs_max_items",
        ),
        Index("ix_collection_configs_owner_id", "owner_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    item_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    query: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    window: Mapped[str] = mapped_column(String(32), nullable=False, default="7d")
    sort_metric: Mapped[str] = mapped_column(String(64), nullable=False, default="total_sales")
    max_items: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    schedule: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    provider_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    runs = relationship("CollectionRun", back_populates="config")


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'partial', 'failed')",
            name="ck_collection_runs_status",
        ),
        Index("ix_collection_runs_config_id", "config_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("collection_configs.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    actual_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor_checkpoint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_response_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scope_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dropped_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dropped_invalid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    config = relationship("CollectionConfig", back_populates="runs")
    items = relationship("SourceItem", back_populates="run")


class SourceItem(Base):
    __tablename__ = "source_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "platform",
            "item_kind",
            "external_id",
            name="uq_source_items_run_platform_kind_ext",
        ),
        CheckConstraint(
            "item_kind IN ('product', 'post')",
            name="ck_source_items_item_kind",
        ),
        Index("ix_source_items_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("collection_runs.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    item_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    raw_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_payload_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rights_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="provider_terms")

    run = relationship("CollectionRun", back_populates="items")
