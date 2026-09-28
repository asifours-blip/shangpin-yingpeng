"""自家商品与不可变事实版本。"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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


class OwnedProduct(Base):
    __tablename__ = "owned_products"
    __table_args__ = (
        UniqueConstraint("owner_id", "sku", name="uq_owned_products_owner_sku"),
        Index("ix_owned_products_owner_id", "owner_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    primary_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("image_assets.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    fact_versions = relationship("ProductFactVersion", back_populates="product")


class ProductFactVersion(Base):
    __tablename__ = "product_fact_versions"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "version",
            name="uq_product_fact_versions_product_version",
        ),
        Index("ix_product_fact_versions_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("owned_products.id"), nullable=False)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    claim_evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    product = relationship("OwnedProduct", back_populates="fact_versions")
