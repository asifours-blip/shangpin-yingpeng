"""活动：创建、幂等启动、查看步骤、审核版本。发布安排不代替平台发出去。"""

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_unfrozen
from app.core.config import settings
from app.core.db import get_db
from app.models import User
from app.models.campaign import Campaign
from app.models.publish import PublishJob
from app.schemas.campaign import CampaignCreateIn, CampaignOut, RunOut, StepOut, VariantOut
from app.services.campaign_service import (
    CampaignNotFound,
    create_campaign,
    load_campaign,
    start_campaign,
)
from app.services.storyboard_review import change_shot, start_storyboard
from app.services.variant_review import (
    ReviewBlocked,
    VersionConflict,
    approve_variant,
    edit_variant,
    fork_facts,
    redo_variant,
    reject_variant,
    review_payload,
)
from app.services.publish_jobs import (
    PublishBlocked,
    cancel_job,
    job_public,
    publish_desk,
    reconfirm_job,
    revoke_approval,
    schedule_job,
    submission_notice,
)
from app.services.operations import overview
from app.services.delivery import DeliveryBlocked, archive_response, build_archive, freeze_delivery

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class VariantEditIn(BaseModel):
    expected_version: int | None = None
    title: str | None = None
    body: str | None = None
    hashtags: list[str] | None = None


class ReviewDecisionIn(BaseModel):
    expected_version: int | None = None
    comment: str | None = None


class RedoIn(BaseModel):
    expected_version: int | None = None
    step_keys: list[str] = Field(default_factory=list)
    comment: str | None = None


class StoryboardActionIn(BaseModel):
    expected_version: int | None = None
    prompt: str | None = Field(default=None, max_length=4000)
    accepted: bool | None = None
    stage: str | None = None


class FactForkIn(BaseModel):
    expected_fact_version: int
    facts: dict


class ScheduleIn(BaseModel):
    platform: str
    expected_version: int
    scheduled_at: datetime
    connection_id: int | None = None
    idempotency_key: str | None = None


class ReconfirmIn(BaseModel):
    scheduled_at: datetime


def _expected(header: str | None, body_version: int | None) -> int:
    parsed = None
    if header is not None and header.strip():
        token = header.strip().strip('"')
        try:
            parsed = int(token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="If-Match 必须是版本号") from exc
    if parsed is not None and body_version is not None and parsed != body_version:
        raise HTTPException(status_code=400, detail="expected_version 与 If-Match 不一致")
    value = parsed if parsed is not None else body_version
    if value is None:
        raise HTTPException(status_code=400, detail="需要 expected_version 或 If-Match")
    return value


def _guard(exc: Exception) -> None:
    if isinstance(exc, CampaignNotFound):
        raise HTTPException(status_code=404, detail=exc.message) from exc
    if isinstance(exc, VersionConflict):
        raise HTTPException(status_code=409, detail=exc.message) from exc
    if isinstance(exc, ReviewBlocked):
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message}) from exc
    raise exc


def _publish_guard(exc: Exception) -> None:
    if isinstance(exc, PublishBlocked):
        status_code = 422
        if exc.code in {"job_missing", "account_missing"}:
            status_code = 404
        elif exc.code in {"maybe_submitted", "idempotency_conflict"}:
            status_code = 409
        raise HTTPException(status_code=status_code, detail={"code": exc.code, "message": exc.message}) from exc
    _guard(exc)


def _detail(campaign, run, steps, variants) -> CampaignOut:
    run_out = None
    if run is not None:
        run_out = RunOut(
            id=run.id,
            recipe_version=run.recipe_version,
            generation_budget=run.generation_budget,
            budget_reserved=run.budget_reserved,
            idempotency_key=run.idempotency_key,
            started_at=run.started_at,
            finished_at=run.finished_at,
            steps=[StepOut.model_validate(step) for step in steps],
        )
    return CampaignOut(
        id=campaign.id,
        product_id=campaign.product_id,
        fact_version_id=campaign.fact_version_id,
        status=campaign.status,
        brief=campaign.brief or {},
        selected_source_item_ids=list(campaign.selected_source_item_ids or []),
        target_platforms=list(
            (campaign.brief or {}).get("target_platforms")
            or dict.fromkeys(row.platform for row in variants)
        ),
        generation_connection="configured" if settings.ARK_API_KEY else "pending_connection",
        variants=[VariantOut.model_validate(row) for row in variants],
        run=run_out,
    )


@router.get("")
def list_campaigns(
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    query = select(Campaign).order_by(Campaign.id.desc())
    if user.role != "admin":
        query = query.where(Campaign.owner_id == user.id)
    rows = list(db.scalars(query).all())
    return {
        "items": [
            {"id": row.id, "status": row.status, "product_id": row.product_id, "fact_version_id": row.fact_version_id}
            for row in rows
        ]
    }


@router.get("/overview")
def get_overview(
    bucket: str | None = None,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return overview(db, user, bucket=bucket)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=CampaignOut)
def create(
    body: CampaignCreateIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> CampaignOut:
    try:
        campaign = create_campaign(
            db,
            user,
            product_id=body.product_id,
            fact_version_id=body.fact_version_id,
            source_item_ids=body.source_item_ids,
            target_platforms=body.target_platforms,
            generation_requirements=body.generation_requirements.strip(),
            generation_budget=body.generation_budget,
        )
    except CampaignNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    campaign, run, steps, variants = load_campaign(db, user, campaign.id)
    return _detail(campaign, run, steps, variants)


@router.post("/{campaign_id}/start", response_model=CampaignOut)
def start(
    campaign_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CampaignOut:
    try:
        start_campaign(db, user, campaign_id, idempotency_key=idempotency_key)
        campaign, run, steps, variants = load_campaign(db, user, campaign_id)
    except CampaignNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return _detail(campaign, run, steps, variants)


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(
    campaign_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> CampaignOut:
    try:
        campaign, run, steps, variants = load_campaign(db, user, campaign_id)
    except CampaignNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
    return _detail(campaign, run, steps, variants)


def _export_error(exc: DeliveryBlocked) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
        headers={"X-Export-Error-Code": exc.code, "Cache-Control": "private, no-store"},
    )


@router.head("/{campaign_id}/variants/{platform}/export")
def preflight_export(
    campaign_id: int,
    platform: str,
    version: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> Response:
    try:
        freeze_delivery(db, user, campaign_id, platform, version)
    except DeliveryBlocked as exc:
        raise _export_error(exc) from exc
    return Response(status_code=200, headers={"Cache-Control": "private, no-store"})


@router.get("/{campaign_id}/variants/{platform}/export")
def export_variant(
    campaign_id: int,
    platform: str,
    version: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
):
    try:
        snapshot = freeze_delivery(db, user, campaign_id, platform, version)
        path = build_archive(snapshot)
    except DeliveryBlocked as exc:
        raise _export_error(exc) from exc
    except OSError as exc:
        raise _export_error(DeliveryBlocked("archive_unavailable", "导出临时空间不可用，请稍后重试", 503)) from exc
    try:
        return archive_response(path, snapshot)
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.post("/{campaign_id}/storyboard/start")
def start_three_shots(
    campaign_id: int,
    body: StoryboardActionIn,
    if_match: str | None = Header(default=None),
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    try:
        created = start_storyboard(db, user, campaign_id, _expected(if_match, body.expected_version))
        return {"id": created.id, "version": created.version}
    except Exception as exc:
        _guard(exc)


@router.post("/{campaign_id}/storyboard/shots/{shot_index}/{action}")
def update_storyboard_shot(
    campaign_id: int,
    shot_index: int,
    action: str,
    body: StoryboardActionIn,
    if_match: str | None = Header(default=None),
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    try:
        created = change_shot(
            db, user, campaign_id, _expected(if_match, body.expected_version), shot_index,
            action, prompt=body.prompt, accepted=body.accepted, stage=body.stage,
        )
        return {"id": created.id, "version": created.version}
    except Exception as exc:
        _guard(exc)


@router.get("/{campaign_id}/review")
def get_review(
    campaign_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return review_payload(db, user, campaign_id)
    except (CampaignNotFound, VersionConflict, ReviewBlocked) as exc:
        _guard(exc)
        raise


@router.patch("/{campaign_id}/variants/{platform}")
def patch_variant(
    campaign_id: int,
    platform: str,
    body: VariantEditIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict:
    try:
        created = edit_variant(
            db,
            user,
            campaign_id,
            platform,
            expected_version=_expected(if_match, body.expected_version),
            title=body.title,
            body=body.body,
            hashtags=body.hashtags,
        )
    except (CampaignNotFound, VersionConflict, ReviewBlocked) as exc:
        _guard(exc)
        raise
    return {
        "id": created.id,
        "platform": created.platform,
        "version": created.version,
        "status": created.status,
        "notice": submission_notice(db, campaign_id, platform),
    }


@router.post("/{campaign_id}/variants/{platform}/approve")
def approve(
    campaign_id: int,
    platform: str,
    body: ReviewDecisionIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict:
    try:
        row = approve_variant(
            db,
            user,
            campaign_id,
            platform,
            expected_version=_expected(if_match, body.expected_version),
            comment=body.comment,
        )
    except (CampaignNotFound, VersionConflict, ReviewBlocked) as exc:
        _guard(exc)
        raise
    return {
        "id": row.id,
        "variant_id": row.variant_id,
        "decision": row.decision,
        "notice": submission_notice(db, campaign_id, platform),
    }


@router.post("/{campaign_id}/variants/{platform}/reject")
def reject(
    campaign_id: int,
    platform: str,
    body: ReviewDecisionIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict:
    try:
        row = reject_variant(
            db,
            user,
            campaign_id,
            platform,
            expected_version=_expected(if_match, body.expected_version),
            comment=body.comment,
        )
    except (CampaignNotFound, VersionConflict, ReviewBlocked) as exc:
        _guard(exc)
        raise
    return {
        "id": row.id,
        "variant_id": row.variant_id,
        "decision": row.decision,
        "notice": submission_notice(db, campaign_id, platform),
    }


@router.post("/{campaign_id}/variants/{platform}/redo")
def redo(
    campaign_id: int,
    platform: str,
    body: RedoIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict:
    try:
        created = redo_variant(
            db,
            user,
            campaign_id,
            platform,
            expected_version=_expected(if_match, body.expected_version),
            step_keys=body.step_keys,
            comment=body.comment,
        )
    except (CampaignNotFound, VersionConflict, ReviewBlocked) as exc:
        _guard(exc)
        raise
    return {
        "id": created.id,
        "platform": created.platform,
        "version": created.version,
        "status": created.status,
        "notice": submission_notice(db, campaign_id, platform),
    }


@router.post("/{campaign_id}/facts")
def update_facts(
    campaign_id: int,
    body: FactForkIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    try:
        created = fork_facts(
            db,
            user,
            campaign_id,
            expected_fact_version=body.expected_fact_version,
            facts=body.facts,
        )
    except (CampaignNotFound, VersionConflict, ReviewBlocked) as exc:
        _guard(exc)
        raise
    return {
        "id": created.id,
        "version": created.version,
        "facts": created.facts,
        "notice": submission_notice(db, campaign_id, None),
    }


@router.get("/{campaign_id}/publish")
def get_publish(
    campaign_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return publish_desk(db, user, campaign_id)
    except (CampaignNotFound, VersionConflict, ReviewBlocked, PublishBlocked) as exc:
        _publish_guard(exc)
        raise


@router.post("/{campaign_id}/publish")
def create_publish_job(
    campaign_id: int,
    body: ScheduleIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    try:
        job = schedule_job(
            db,
            user,
            campaign_id,
            body.platform,
            expected_version=body.expected_version,
            scheduled_at=body.scheduled_at,
            connection_id=body.connection_id,
            idempotency_key=body.idempotency_key or idempotency_key,
        )
    except (CampaignNotFound, VersionConflict, ReviewBlocked, PublishBlocked) as exc:
        _publish_guard(exc)
        raise
    return job_public(job)


def _owned_job(db: Session, user: User, campaign_id: int, job_id: int) -> None:
    row = db.get(PublishJob, job_id)
    if row is None or row.owner_id != user.id or row.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="发布任务不存在")


@router.post("/{campaign_id}/publish/{job_id}/cancel")
def cancel_publish_job(
    campaign_id: int,
    job_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    _owned_job(db, user, campaign_id, job_id)
    try:
        job = cancel_job(db, user, job_id)
    except (CampaignNotFound, VersionConflict, ReviewBlocked, PublishBlocked) as exc:
        _publish_guard(exc)
        raise
    return job_public(job)


@router.post("/{campaign_id}/publish/{job_id}/reconfirm")
def reconfirm_publish_job(
    campaign_id: int,
    job_id: int,
    body: ReconfirmIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> dict:
    _owned_job(db, user, campaign_id, job_id)
    try:
        job = reconfirm_job(db, user, job_id, scheduled_at=body.scheduled_at)
    except (CampaignNotFound, VersionConflict, ReviewBlocked, PublishBlocked) as exc:
        _publish_guard(exc)
        raise
    return job_public(job)


@router.post("/{campaign_id}/variants/{platform}/revoke")
def revoke(
    campaign_id: int,
    platform: str,
    body: ReviewDecisionIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict:
    try:
        return revoke_approval(
            db,
            user,
            campaign_id,
            platform,
            expected_version=_expected(if_match, body.expected_version),
        )
    except (CampaignNotFound, VersionConflict, ReviewBlocked, PublishBlocked) as exc:
        _publish_guard(exc)
        raise
