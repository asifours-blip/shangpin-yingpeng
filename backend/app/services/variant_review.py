"""审核绑定具体变体版本。新版本不继承旧批准。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import (
    Campaign,
    CampaignRun,
    ContentVariant,
    PipelineStep,
    VariantAsset,
    VariantReview,
)
from app.models.product import OwnedProduct, ProductFactVersion
from app.models.user import User
from app.services import storage
from app.services.campaign_pipeline import STEP_DEPS
from app.services.campaign_service import CampaignNotFound, _owns
from app.services.consumer_qc import screen_consumer_copy

PLATFORM_STEPS = {
    "douyin": ("copy:douyin", "image:douyin", "video:douyin"),
    "xiaohongshu": ("copy:xiaohongshu", "cards:xiaohongshu"),
}
MEDIA_OF = {
    "douyin": ("image:douyin", "video:douyin"),
    "xiaohongshu": ("cards:xiaohongshu",),
}
REGEN_ROLES = {
    "image:douyin": {"cover"},
    "video:douyin": {"final_video"},
    "cards:xiaohongshu": {"cover", "card"},
}
BLOCKING_QC = {
    "content_insufficient",
    "title_overflow",
    "body_overflow",
    "duration_overflow",
    "duration_out_of_range",
}
ROLE_LABEL = {
    "cover": "封面",
    "card": "内容卡",
    "clip": "片段",
    "final_video": "成片",
}


class VersionConflict(Exception):
    def __init__(self, message: str = "版本已变化") -> None:
        self.message = message
        super().__init__(message)


class ReviewBlocked(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _campaign(db: Session, user: User, campaign_id: int) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or not _owns(user, campaign.owner_id):
        raise CampaignNotFound("活动不存在")
    return campaign


def _run(db: Session, campaign_id: int) -> CampaignRun:
    run = db.scalar(
        select(CampaignRun).where(CampaignRun.campaign_id == campaign_id).order_by(CampaignRun.id.asc())
    )
    if run is None:
        raise CampaignNotFound("活动不存在")
    return run


def _current(db: Session, campaign_id: int, platform: str) -> ContentVariant:
    variant = db.scalar(
        select(ContentVariant)
        .where(ContentVariant.campaign_id == campaign_id, ContentVariant.platform == platform)
        .order_by(ContentVariant.version.desc())
    )
    if variant is None:
        raise CampaignNotFound("变体不存在")
    return variant


def _require_version(variant: ContentVariant, expected_version: int) -> None:
    if variant.version != expected_version:
        raise VersionConflict("版本已变化")


def _release(db: Session, exc: Exception) -> None:
    db.rollback()
    raise exc


def _commit_locked(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise VersionConflict("版本已变化") from exc


def _lock_campaign(db: Session, user: User, campaign_id: int) -> Campaign:
    """版本检查和写入都在这把活动行锁里完成。后到的事务会看到先提交的版本。"""
    db.execute(text("SET LOCAL lock_timeout = '8s'"))
    campaign = db.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if campaign is None or not _owns(user, campaign.owner_id):
        _release(db, CampaignNotFound("活动不存在"))
    return campaign


def _locked_current(
    db: Session,
    campaign_id: int,
    platform: str,
    expected_version: int,
) -> ContentVariant:
    variant = db.scalar(
        select(ContentVariant)
        .where(ContentVariant.campaign_id == campaign_id, ContentVariant.platform == platform)
        .order_by(ContentVariant.version.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if variant is None:
        _release(db, CampaignNotFound("变体不存在"))
    if variant.version != expected_version:
        _release(db, VersionConflict("版本已变化"))
    return variant


def _fact(db: Session, variant: ContentVariant, campaign: Campaign) -> ProductFactVersion:
    fact_id = variant.fact_version_id or campaign.fact_version_id
    fact = db.get(ProductFactVersion, fact_id)
    if fact is None:
        raise CampaignNotFound("事实版本不存在")
    return fact


def _assets(db: Session, variant_id: int) -> list[VariantAsset]:
    return list(
        db.scalars(
            select(VariantAsset).where(VariantAsset.variant_id == variant_id).order_by(VariantAsset.position.asc())
        ).all()
    )


def _latest_platform_steps(db: Session, run_id: int, platform: str) -> list[PipelineStep]:
    wanted = set(PLATFORM_STEPS[platform])
    rows = list(
        db.scalars(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key.in_(wanted),
            )
        ).all()
    )
    latest: dict[str, PipelineStep] = {}
    for step in rows:
        current = latest.get(step.step_key)
        if current is None or step.version > current.version:
            latest[step.step_key] = step
    return [latest[key] for key in PLATFORM_STEPS[platform] if key in latest]


def approval_blockers(
    db: Session, campaign: Campaign, variant: ContentVariant, *, check_storage: bool = True,
) -> list[dict]:
    blockers: list[dict] = []
    run = _run(db, campaign.id)
    steps = _latest_platform_steps(db, run.id, variant.platform)
    expected = PLATFORM_STEPS[variant.platform]
    by_key = {step.step_key: step for step in steps}
    if any(key not in by_key or by_key[key].status != "succeeded" for key in expected):
        blockers.append({"code": "generation_incomplete", "message": "这一侧生成还没完成"})
    assets = _assets(db, variant.id)
    roles = {item.role for item in assets}
    if variant.platform == "douyin":
        if "cover" not in roles or "final_video" not in roles:
            blockers.append({"code": "assets_missing", "message": "抖音封面或成片缺失"})
    else:
        if "cover" not in roles or "card" not in roles:
            blockers.append({"code": "assets_missing", "message": "小红书封面或内容卡缺失"})
    if check_storage:
        blockers.extend(_storage_blockers(db, assets))
    for step in steps:
        output = step.output or {}
        issue = output.get("qc_issue") or step.error_code
        if issue in BLOCKING_QC:
            blockers.append({"code": issue, "message": "质检未通过，不能批准"})
        if output.get("unplaced"):
            blockers.append({"code": "text_overflow", "message": "有文字溢出，不能批准"})
    qc = variant.qc_result or {}
    if qc.get("qc_issue") in BLOCKING_QC:
        blockers.append({"code": qc["qc_issue"], "message": "质检未通过，不能批准"})
    if qc.get("unplaced"):
        blockers.append({"code": "text_overflow", "message": "有文字溢出，不能批准"})
    fact = _fact(db, variant, campaign)
    issues = screen_consumer_copy(
        title=variant.title or "",
        body=variant.body or "",
        hashtags=list(variant.hashtags or []),
        facts=dict(fact.facts or {}),
    )["issues"]
    for issue in issues:
        blockers.append({"code": issue["code"], "message": issue["reason"]})
    if not (variant.title or "").strip() or not (variant.body or "").strip():
        blockers.append({"code": "generation_incomplete", "message": "文案还没写完"})
    return blockers


def _storage_blockers(db: Session, assets: list[VariantAsset]) -> list[dict]:
    """对象必须能从当前配置的存储读出来。读不到就不能批准。"""
    blockers = []
    for item in assets:
        from app.models import ImageAsset

        asset = db.get(ImageAsset, item.asset_id)
        label = ROLE_LABEL.get(item.role, item.role)
        if asset is None:
            blockers.append({"code": "asset_unreadable", "message": f"{label}没有资产记录，不能批准"})
            continue
        try:
            exists = storage.object_exists(asset.object_key)
        except Exception:
            blockers.append({"code": "asset_unreadable", "message": f"{label}无法向对象存储确认，不能批准"})
            continue
        if not exists:
            blockers.append({"code": "asset_unreadable", "message": f"{label}在对象存储中不存在，不能批准"})
            continue
        try:
            payload = storage.get_bytes(asset.object_key)
        except Exception:
            blockers.append({"code": "asset_unreadable", "message": f"{label}无法从对象存储读取，不能批准"})
            continue
        if not payload:
            blockers.append({"code": "asset_unreadable", "message": f"{label}在对象存储里是空文件，不能批准"})
    return blockers


def _snapshot(db: Session, variant: ContentVariant, fact: ProductFactVersion) -> dict:
    return {
        "copy_snapshot": {
            "version": variant.version,
            "title": variant.title,
            "body": variant.body,
            "hashtags": list(variant.hashtags or []),
        },
        "asset_order": [
            {"asset_id": item.asset_id, "role": item.role, "position": item.position}
            for item in _assets(db, variant.id)
        ],
        "fact_version_id": fact.id,
        "fact_snapshot": dict(fact.facts or {}),
        "qc_snapshot": dict(variant.qc_result or {}),
    }


def _record(
    db: Session,
    *,
    user: User,
    variant: ContentVariant,
    fact: ProductFactVersion,
    decision: str,
    comment: str | None,
) -> VariantReview:
    snap = _snapshot(db, variant, fact)
    row = VariantReview(
        variant_id=variant.id,
        version=variant.version,
        reviewer_id=user.id,
        decision=decision,
        comment=(comment or "").strip() or None,
        reviewed_at=datetime.now(timezone.utc),
        **snap,
    )
    db.add(row)
    return row


def _reuse_assets(db: Session, old: ContentVariant, new: ContentVariant, expanded: list[str]) -> None:
    """没被重做的媒体留在新版本上。要重画的角色不带走旧文件。"""
    drop: set[str] = set()
    for key in expanded:
        drop.update(REGEN_ROLES.get(key, set()))
    for item in _assets(db, old.id):
        if item.role in drop:
            continue
        db.add(
            VariantAsset(
                variant_id=new.id,
                asset_id=item.asset_id,
                role=item.role,
                position=item.position,
            )
        )


def _sync_campaign(db: Session, campaign: Campaign) -> None:
    """活动状态从两侧当前版本推导。一侧退回不改另一侧的批准。"""
    if campaign.status == "blocked":
        return
    db.flush()
    currents = []
    for platform in ("douyin", "xiaohongshu"):
        row = db.scalar(
            select(ContentVariant)
            .where(ContentVariant.campaign_id == campaign.id, ContentVariant.platform == platform)
            .order_by(ContentVariant.version.desc())
        )
        if row is not None:
            currents.append(row)
    run = _run(db, campaign.id)
    latest = []
    for platform in ("douyin", "xiaohongshu"):
        latest.extend(_latest_platform_steps(db, run.id, platform))
    if any(step.status in {"pending", "running"} for step in latest):
        campaign.status = "generating"
        return
    if any(step.status == "failed" for step in latest):
        campaign.status = "failed"
        return
    if currents and all(item.status == "approved" for item in currents):
        campaign.status = "approved"
        return
    campaign.status = "needs_review"


def _queue(
    db: Session,
    run: CampaignRun,
    variant: ContentVariant,
    keys: list[str],
    *,
    human_step_id: int | None = None,
) -> None:
    for key in keys:
        exists = db.scalar(
            select(PipelineStep.id).where(
                PipelineStep.run_id == run.id,
                PipelineStep.step_key == key,
                PipelineStep.version == variant.version,
            )
        )
        if exists is not None:
            continue
        db.add(
            PipelineStep(
                run_id=run.id,
                step_key=key,
                variant_platform=variant.platform,
                version=variant.version,
                status="pending",
                depends_on=list(STEP_DEPS[key]),
                output={},
                variant_id=variant.id,
                input_hash=None if human_step_id is None else f"human:{human_step_id}",
            )
        )


def _record_human_copy(
    db: Session,
    run: CampaignRun,
    variant: ContentVariant,
    user: User,
    *,
    title: str | None,
    body: str | None,
    hashtags: list[str],
) -> PipelineStep:
    """新版本自己的文案输入。旧的失败任务保持原样。"""
    key = f"copy:{variant.platform}"
    if key not in STEP_DEPS:
        _release(db, ReviewBlocked("redo_empty", "没有这一侧的文案步骤"))
    row = PipelineStep(
        run_id=run.id,
        step_key=key,
        variant_platform=variant.platform,
        version=variant.version,
        status="succeeded",
        depends_on=list(STEP_DEPS[key]),
        output={
            "source": "human_edit",
            "editor_id": user.id,
            "title": title,
            "body": body,
            "hashtags": list(hashtags),
        },
        variant_id=variant.id,
        copywriting_operation_id=None,
        provider_request_id=None,
        local_request_id=None,
    )
    db.add(row)
    db.flush()
    return row


def _fork_variant(
    db: Session,
    current: ContentVariant,
    *,
    title: str | None,
    body: str | None,
    hashtags: list | None,
    fact_version_id: int | None,
    clear_copy: bool,
) -> ContentVariant:
    row = ContentVariant(
        campaign_id=current.campaign_id,
        platform=current.platform,
        content_type=current.content_type,
        version=current.version + 1,
        title=None if clear_copy else (current.title if title is None else title),
        body=None if clear_copy else (current.body if body is None else body),
        hashtags=[] if clear_copy else (list(current.hashtags or []) if hashtags is None else list(hashtags)),
        status="draft",
        qc_result=None,
        storyboard=None,
        fact_version_id=fact_version_id or current.fact_version_id,
    )
    db.add(row)
    db.flush()
    return row


def edit_variant(
    db: Session,
    user: User,
    campaign_id: int,
    platform: str,
    *,
    expected_version: int,
    title: str | None,
    body: str | None,
    hashtags: list[str] | None,
) -> ContentVariant:
    campaign = _lock_campaign(db, user, campaign_id)
    current = _locked_current(db, campaign_id, platform, expected_version)
    fact = _fact(db, current, campaign)
    facts = dict(fact.facts or {})
    next_title = current.title if title is None else title
    next_body = current.body if body is None else body
    next_tags = list(current.hashtags or []) if hashtags is None else list(hashtags)
    issues = screen_consumer_copy(
        title=next_title or "",
        body=next_body or "",
        hashtags=next_tags,
        facts=facts,
    )["issues"]
    if issues:
        _release(db, ReviewBlocked(issues[0]["code"], issues[0]["reason"]))
    from app.services.publish_jobs import invalidate_unsubmitted

    invalidate_unsubmitted(db, current.id)
    created = _fork_variant(
        db,
        current,
        title=next_title,
        body=next_body,
        hashtags=next_tags,
        fact_version_id=fact.id,
        clear_copy=False,
    )
    run = _run(db, campaign.id)
    human = _record_human_copy(
        db,
        run,
        created,
        user,
        title=next_title,
        body=next_body,
        hashtags=next_tags,
    )
    _queue(db, run, created, list(MEDIA_OF[platform]), human_step_id=human.id)
    _sync_campaign(db, campaign)
    _commit_locked(db)
    db.refresh(created)
    return created


def approve_variant(
    db: Session,
    user: User,
    campaign_id: int,
    platform: str,
    *,
    expected_version: int,
    comment: str | None,
) -> VariantReview:
    campaign = _lock_campaign(db, user, campaign_id)
    current = _locked_current(db, campaign_id, platform, expected_version)
    bound_id = current.id
    blockers = approval_blockers(db, campaign, current)
    if blockers:
        _release(db, ReviewBlocked(blockers[0]["code"], blockers[0]["message"]))
    if current.id != bound_id or current.version != expected_version:
        _release(db, VersionConflict("版本已变化"))
    fact = _fact(db, current, campaign)
    row = _record(db, user=user, variant=current, fact=fact, decision="approved", comment=comment)
    current.status = "approved"
    _sync_campaign(db, campaign)
    _commit_locked(db)
    db.refresh(row)
    return row


def reject_variant(
    db: Session,
    user: User,
    campaign_id: int,
    platform: str,
    *,
    expected_version: int,
    comment: str | None,
) -> VariantReview:
    campaign = _lock_campaign(db, user, campaign_id)
    current = _locked_current(db, campaign_id, platform, expected_version)
    fact = _fact(db, current, campaign)
    from app.services.publish_jobs import invalidate_unsubmitted

    invalidate_unsubmitted(db, current.id)
    row = _record(db, user=user, variant=current, fact=fact, decision="rejected", comment=comment)
    current.status = "rejected"
    _sync_campaign(db, campaign)
    _commit_locked(db)
    db.refresh(row)
    return row


def redo_variant(
    db: Session,
    user: User,
    campaign_id: int,
    platform: str,
    *,
    expected_version: int,
    step_keys: list[str],
    comment: str | None,
) -> ContentVariant:
    campaign = _lock_campaign(db, user, campaign_id)
    current = _locked_current(db, campaign_id, platform, expected_version)
    run = _run(db, campaign.id)
    if any(step.status == "unknown" for step in _latest_platform_steps(db, run.id, platform)):
        _release(db, ReviewBlocked("result_unknown", "这一侧存在结果未知的生成请求，先人工核验，不能重做"))
    allowed = set(PLATFORM_STEPS[platform])
    chosen = [key for key in step_keys if key in allowed]
    if not chosen:
        _release(db, ReviewBlocked("redo_empty", "没有可重做的步骤"))
    from app.services.publish_jobs import invalidate_unsubmitted

    invalidate_unsubmitted(db, current.id)
    fact = _fact(db, current, campaign)
    _record(db, user=user, variant=current, fact=fact, decision="rejected", comment=comment or "退回重做")
    current.status = "rejected"
    expanded = []
    for key in chosen:
        if key not in expanded:
            expanded.append(key)
        for later in PLATFORM_STEPS[platform][PLATFORM_STEPS[platform].index(key) + 1 :]:
            if later not in expanded:
                expanded.append(later)
    created = _fork_variant(
        db,
        current,
        title=None,
        body=None,
        hashtags=None,
        fact_version_id=fact.id,
        clear_copy=any(key.startswith("copy:") for key in expanded),
    )
    if not any(key.startswith("copy:") for key in expanded):
        created.title = current.title
        created.body = current.body
        created.hashtags = list(current.hashtags or [])
    _reuse_assets(db, current, created, expanded)
    _queue(db, run, created, expanded)
    _sync_campaign(db, campaign)
    _commit_locked(db)
    db.refresh(created)
    return created


def fork_facts(
    db: Session,
    user: User,
    campaign_id: int,
    *,
    expected_fact_version: int,
    facts: dict,
) -> ProductFactVersion:
    campaign = _lock_campaign(db, user, campaign_id)
    current_fact = db.scalar(
        select(ProductFactVersion)
        .where(ProductFactVersion.id == campaign.fact_version_id)
        .with_for_update()
    )
    if current_fact is None or current_fact.version != expected_fact_version:
        _release(db, VersionConflict("事实版本已变化"))
    run = _run(db, campaign.id)
    platforms = list(
        db.scalars(select(ContentVariant.platform).where(ContentVariant.campaign_id == campaign.id).distinct()).all()
    )
    if any(
        step.status == "unknown"
        for platform in platforms
        for step in _latest_platform_steps(db, run.id, platform)
    ):
        _release(db, ReviewBlocked("result_unknown", "存在结果未知的生成请求，先人工核验，不能重排生成"))
    previous = dict(current_fact.facts or {})
    created = ProductFactVersion(
        product_id=current_fact.product_id,
        facts=dict(facts),
        claim_evidence=dict(current_fact.claim_evidence or {}),
        version=current_fact.version + 1,
    )
    db.add(created)
    db.flush()
    if current_fact.facts != previous:
        _release(db, ReviewBlocked("fact_mutated", "旧事实快照被改写"))
    campaign.fact_version_id = created.id
    from app.services.publish_jobs import invalidate_unsubmitted

    for platform in platforms:
        current = _current(db, campaign.id, platform)
        invalidate_unsubmitted(db, current.id)
        forked = _fork_variant(
            db,
            current,
            title=None,
            body=None,
            hashtags=None,
            fact_version_id=created.id,
            clear_copy=True,
        )
        _queue(db, run, forked, list(PLATFORM_STEPS[platform]))
    _sync_campaign(db, campaign)
    _commit_locked(db)
    db.refresh(created)
    db.refresh(current_fact)
    return created


def review_payload(db: Session, user: User, campaign_id: int) -> dict:
    campaign = _campaign(db, user, campaign_id)
    run = _run(db, campaign.id)
    fact = db.get(ProductFactVersion, campaign.fact_version_id)
    product = db.get(OwnedProduct, campaign.product_id)
    platforms = []
    for platform in ("douyin", "xiaohongshu"):
        rows = list(
            db.scalars(
                select(ContentVariant)
                .where(ContentVariant.campaign_id == campaign.id, ContentVariant.platform == platform)
                .order_by(ContentVariant.version.asc())
            ).all()
        )
        if not rows:
            continue
        current = rows[-1]
        reviews = list(
            db.scalars(
                select(VariantReview)
                .where(VariantReview.variant_id.in_([row.id for row in rows]))
                .order_by(VariantReview.id.asc())
            ).all()
        )
        platforms.append(
            {
                "platform": platform,
                "current": _variant_dict(db, current),
                "versions": [_variant_dict(db, row) for row in rows],
                "reviews": [_review_dict(row) for row in reviews],
                "blockers": approval_blockers(db, campaign, current),
                "steps": [
                    {
                        "step_key": step.step_key,
                        "version": step.version,
                        "status": step.status,
                        "error_code": step.error_code,
                        "variant_id": step.variant_id,
                        "qc_issue": (step.output or {}).get("qc_issue"),
                        "review_notes": (step.output or {}).get("review_notes") or [],
                        "lines": (step.output or {}).get("lines") or [],
                        "source": (step.output or {}).get("source"),
                    }
                    for step in _latest_platform_steps(db, run.id, platform)
                ],
            }
        )
    return {
        "campaign_id": campaign.id,
        "status": campaign.status,
        "fact_version_id": campaign.fact_version_id,
        "fact_version": None if fact is None else fact.version,
        "facts": {} if fact is None else dict(fact.facts or {}),
        "product_name": "" if product is None else product.name,
        "primary_asset_id": None if product is None else product.primary_asset_id,
        "platforms": platforms,
        "ark_live": "pending",
    }


def _variant_dict(db: Session, variant: ContentVariant) -> dict:
    return {
        "id": variant.id,
        "platform": variant.platform,
        "version": variant.version,
        "title": variant.title,
        "body": variant.body,
        "hashtags": list(variant.hashtags or []),
        "status": variant.status,
        "fact_version_id": variant.fact_version_id,
        "qc_result": variant.qc_result,
        "assets": [
            {"asset_id": item.asset_id, "role": item.role, "position": item.position}
            for item in _assets(db, variant.id)
        ],
    }


def _review_dict(row: VariantReview) -> dict:
    return {
        "id": row.id,
        "variant_id": row.variant_id,
        "version": row.version,
        "reviewer_id": row.reviewer_id,
        "decision": row.decision,
        "comment": row.comment,
        "reviewed_at": row.reviewed_at,
        "copy_snapshot": row.copy_snapshot,
        "asset_order": row.asset_order,
        "fact_version_id": row.fact_version_id,
        "fact_snapshot": row.fact_snapshot,
        "qc_snapshot": row.qc_snapshot,
    }
