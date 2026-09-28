"""采集配置与批次。无权限时记失败批次，不写入虚构条目。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_unfrozen
from app.core.db import get_db
from app.models import User
from app.models.source import CollectionConfig, CollectionRun, SourceItem
from app.schemas.collection import (
    CollectionConfigIn,
    CollectionConfigListOut,
    CollectionConfigOut,
    CollectionRunCreateIn,
    CollectionRunListOut,
    CollectionRunOut,
    CollectionRunSummaryOut,
    SourceItemListOut,
    SourceItemOut,
)
from app.services.collect import execute_collection

router = APIRouter(prefix="/api/collections", tags=["collections"])


def _config_or_404(db: Session, config_id: int, user: User) -> CollectionConfig:
    row = db.get(CollectionConfig, config_id)
    if row is None or (row.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="采集配置不存在")
    return row


def _run_or_404(db: Session, run_id: int, user: User) -> CollectionRun:
    row = db.scalar(
        select(CollectionRun)
        .join(CollectionConfig, CollectionRun.config_id == CollectionConfig.id)
        .where(CollectionRun.id == run_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="采集批次不存在")
    if row.config.owner_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="采集批次不存在")
    return row


@router.get("/configs", response_model=CollectionConfigListOut)
def list_configs(
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> CollectionConfigListOut:
    stmt = select(CollectionConfig).order_by(CollectionConfig.id.desc())
    if user.role != "admin":
        stmt = stmt.where(CollectionConfig.owner_id == user.id)
    rows = db.scalars(stmt).all()
    return CollectionConfigListOut(items=[CollectionConfigOut.model_validate(row) for row in rows])


@router.post("/configs", response_model=CollectionConfigOut)
def create_config(
    body: CollectionConfigIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> CollectionConfig:
    row = CollectionConfig(
        owner_id=user.id,
        provider=body.provider,
        item_kind=body.item_kind,
        category=body.category.strip(),
        query=body.query.strip(),
        window=body.window.strip() or "7d",
        sort_metric=body.sort_metric.strip() or "total_sales",
        max_items=body.max_items,
        schedule=body.schedule.strip() or "manual",
        enabled=True,
        provider_settings={},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/runs", response_model=CollectionRunOut)
def start_run(
    body: CollectionRunCreateIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> CollectionRun:
    config = _config_or_404(db, body.config_id, user)
    if config.owner_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="采集配置不存在")
    if not config.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="采集配置已停用")
    return execute_collection(db, config)


@router.get("/runs", response_model=CollectionRunListOut)
def list_runs(
    config_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> CollectionRunListOut:
    query = select(CollectionRun, CollectionConfig).join(
        CollectionConfig, CollectionRun.config_id == CollectionConfig.id
    )
    if user.role != "admin":
        query = query.where(CollectionConfig.owner_id == user.id)
    if config_id is not None:
        query = query.where(CollectionConfig.id == config_id)
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = db.execute(
        query.order_by(CollectionRun.id.desc()).offset((page - 1) * per_page).limit(per_page)
    ).all()
    return CollectionRunListOut(
        items=[
            CollectionRunSummaryOut(
                **CollectionRunOut.model_validate(run).model_dump(),
                provider=config.provider,
                item_kind=config.item_kind,
                sort_metric=config.sort_metric,
                window=config.window,
            )
            for run, config in rows
        ],
        total=total,
    )


@router.get("/runs/{run_id}", response_model=CollectionRunOut)
def get_run(
    run_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> CollectionRun:
    return _run_or_404(db, run_id, user)


@router.get("/runs/{run_id}/items", response_model=SourceItemListOut)
def list_items(
    run_id: int,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=100),
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> SourceItemListOut:
    run = _run_or_404(db, run_id, user)
    total = int(
        db.scalar(select(func.count()).select_from(SourceItem).where(SourceItem.run_id == run.id))
        or 0
    )
    rows = db.scalars(
        select(SourceItem)
        .where(SourceItem.run_id == run.id)
        .order_by(SourceItem.source_rank.asc(), SourceItem.id.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()
    return SourceItemListOut(
        items=[SourceItemOut.model_validate(row) for row in rows],
        total=total,
        scope_description=run.scope_description,
        actual_count=run.actual_count,
        status=run.status,
    )
