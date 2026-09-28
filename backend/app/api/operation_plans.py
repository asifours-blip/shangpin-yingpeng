"""Operator plans and auditable rounds. This API never approves or publishes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_unfrozen
from app.core.db import get_db
from app.models import User
from app.models.operation_plan import OperationPlan, OperationPlanRun
from app.schemas.operation_plan import PlanCreateIn, PlanPatchIn, ResolveIn, RunActionIn, WindowIn
from app.services.operation_plans import (
    PlanBlocked, _owned_plan, _owned_run, backfill, create_plan, patch_plan,
    plan_public, resolve_run, resume_run, run_public,
)

router = APIRouter(prefix="/api/operation-plans", tags=["operation-plans"])


def _error(exc: PlanBlocked) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.get("")
def list_plans(user: User = Depends(require_unfrozen), db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(OperationPlan).where(OperationPlan.owner_id == user.id)
                           .order_by(OperationPlan.id.desc())))
    return {"items": [plan_public(row) for row in rows], "total": len(rows)}


@router.post("")
def create(body: PlanCreateIn, user: User = Depends(require_unfrozen), db: Session = Depends(get_db)) -> dict:
    try:
        return plan_public(create_plan(db, user, body))
    except PlanBlocked as exc:
        raise _error(exc) from exc


@router.get("/{plan_id}")
def get_plan(plan_id: int, user: User = Depends(require_unfrozen), db: Session = Depends(get_db)) -> dict:
    try:
        return plan_public(_owned_plan(db, user.id, plan_id))
    except PlanBlocked as exc:
        raise _error(exc) from exc


@router.patch("/{plan_id}")
def patch(plan_id: int, body: PlanPatchIn,
          user: User = Depends(require_unfrozen), db: Session = Depends(get_db)) -> dict:
    try:
        changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
        return plan_public(patch_plan(db, user, plan_id, body.expected_version, changes))
    except PlanBlocked as exc:
        raise _error(exc) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "plan_invalid", "message": str(exc.errors())}) from exc


@router.get("/{plan_id}/runs")
def list_runs(plan_id: int, user: User = Depends(require_unfrozen), db: Session = Depends(get_db)) -> dict:
    try:
        _owned_plan(db, user.id, plan_id)
    except PlanBlocked as exc:
        raise _error(exc) from exc
    rows = list(db.scalars(select(OperationPlanRun).where(OperationPlanRun.plan_id == plan_id)
                           .order_by(OperationPlanRun.scheduled_for.desc(), OperationPlanRun.id.desc())
                           .limit(100)))
    return {"items": [run_public(db, row) for row in rows],
            "total": int(db.scalar(select(func.count()).select_from(OperationPlanRun)
                                   .where(OperationPlanRun.plan_id == plan_id)) or 0)}


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: User = Depends(require_unfrozen), db: Session = Depends(get_db)) -> dict:
    try:
        _plan, run = _owned_run(db, user.id, run_id)
        return run_public(db, run)
    except PlanBlocked as exc:
        raise _error(exc) from exc


@router.post("/{plan_id}/backfill")
def backfill_window(plan_id: int, body: WindowIn,
                    user: User = Depends(require_unfrozen), db: Session = Depends(get_db)) -> dict:
    try:
        return run_public(db, backfill(db, user, plan_id, body.expected_version, body.scheduled_for))
    except PlanBlocked as exc:
        raise _error(exc) from exc


@router.post("/runs/{run_id}/resume")
def resume(run_id: int, body: RunActionIn,
           user: User = Depends(require_unfrozen), db: Session = Depends(get_db)) -> dict:
    try:
        return run_public(db, resume_run(db, user, run_id, body.expected_version))
    except PlanBlocked as exc:
        raise _error(exc) from exc


@router.post("/runs/{run_id}/resolve")
def resolve(run_id: int, body: ResolveIn,
            user: User = Depends(require_unfrozen), db: Session = Depends(get_db)) -> dict:
    try:
        return run_public(db, resolve_run(db, user, run_id, body.expected_version,
                                          body.source_item_id, body.product_id))
    except PlanBlocked as exc:
        raise _error(exc) from exc
