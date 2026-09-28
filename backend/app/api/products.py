"""自家商品。事实只追加版本，不改旧版本。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_unfrozen
from app.core.db import get_db
from app.models import ImageAsset, User
from app.models.product import OwnedProduct, ProductFactVersion
from app.schemas.product import (
    FactVersionOut,
    ProductCreateIn,
    ProductListOut,
    ProductOut,
    ProductPatchIn,
)

router = APIRouter(prefix="/api/products", tags=["products"])


def _product_or_404(db: Session, product_id: int, user: User) -> OwnedProduct:
    row = db.get(OwnedProduct, product_id)
    if row is None or (row.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    return row


def _latest_fact(db: Session, product_id: int) -> ProductFactVersion | None:
    return db.scalar(
        select(ProductFactVersion)
        .where(ProductFactVersion.product_id == product_id)
        .order_by(ProductFactVersion.version.desc())
    )


def _to_out(db: Session, product: OwnedProduct) -> ProductOut:
    latest = _latest_fact(db, product.id)
    return ProductOut(
        id=product.id,
        sku=product.sku,
        name=product.name,
        active=product.active,
        primary_asset_id=product.primary_asset_id,
        latest_fact=FactVersionOut.model_validate(latest) if latest else None,
    )


def _own_asset(db: Session, owner_id: int, asset_id: int | None) -> None:
    if asset_id is None:
        return
    asset = db.get(ImageAsset, asset_id)
    if asset is None or asset.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品图不存在")


@router.get("", response_model=ProductListOut)
def list_products(
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> ProductListOut:
    stmt = select(OwnedProduct).order_by(OwnedProduct.id.desc())
    if user.role != "admin":
        stmt = stmt.where(OwnedProduct.owner_id == user.id)
    rows = db.scalars(stmt).all()
    return ProductListOut(items=[_to_out(db, row) for row in rows])


@router.post("", response_model=ProductOut)
def create_product(
    body: ProductCreateIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> ProductOut:
    _own_asset(db, user.id, body.primary_asset_id)
    product = OwnedProduct(
        owner_id=user.id,
        sku=body.sku.strip(),
        name=body.name.strip(),
        active=True,
        primary_asset_id=body.primary_asset_id,
    )
    db.add(product)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SKU 已存在") from None
    db.add(
        ProductFactVersion(
            product_id=product.id,
            facts=body.facts,
            claim_evidence=body.claim_evidence,
            version=1,
        )
    )
    db.commit()
    db.refresh(product)
    return _to_out(db, product)


@router.patch("/{product_id}", response_model=ProductOut)
def patch_product(
    product_id: int,
    body: ProductPatchIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> ProductOut:
    product = _product_or_404(db, product_id, user)
    if product.owner_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在")
    latest = _latest_fact(db, product.id)
    if body.expected_version is not None and (
        latest is None or latest.version != body.expected_version
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="商品事实版本已变化")
    if body.name is not None:
        product.name = body.name.strip()
    if body.active is not None:
        product.active = body.active
    if body.primary_asset_id is not None:
        _own_asset(db, product.owner_id, body.primary_asset_id)
        product.primary_asset_id = body.primary_asset_id
    if body.facts is not None:
        previous = latest.facts if latest else {}
        evidence = body.claim_evidence if body.claim_evidence is not None else (
            latest.claim_evidence if latest else {}
        )
        if previous != body.facts or (latest and evidence != latest.claim_evidence):
            db.add(
                ProductFactVersion(
                    product_id=product.id,
                    facts=body.facts,
                    claim_evidence=evidence,
                    version=(latest.version if latest else 0) + 1,
                )
            )
    db.commit()
    db.refresh(product)
    return _to_out(db, product)
