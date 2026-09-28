"""创建活动、幂等启动。归属不对一律当不存在。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignRun, ContentVariant, PipelineStep
from app.models.product import OwnedProduct, ProductFactVersion
from app.models.source import CollectionConfig, CollectionRun, SourceItem
from app.models.user import User
from app.services.campaign_pipeline import STEP_DEPS, STEP_PLATFORM, plan_from_snapshot


class CampaignNotFound(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _owns(user: User, owner_id: int) -> bool:
    return user.role == "admin" or user.id == owner_id


def create_campaign(
    db: Session,
    user: User,
    *,
    product_id: int,
    fact_version_id: int,
    source_item_ids: list[int],
    target_platforms: list[str] | None = None,
    generation_requirements: str = "",
    generation_budget: int = 12,
    commit: bool = True,
) -> Campaign:
    product = db.get(OwnedProduct, product_id)
    if product is None or not _owns(user, product.owner_id):
        raise CampaignNotFound("商品不存在")
    fact = db.get(ProductFactVersion, fact_version_id)
    if fact is None or fact.product_id != product.id:
        raise CampaignNotFound("事实版本不存在")
    wanted = list(dict.fromkeys(source_item_ids))
    rows = db.execute(
        select(SourceItem, CollectionConfig.owner_id)
        .join(CollectionRun, SourceItem.run_id == CollectionRun.id)
        .join(CollectionConfig, CollectionRun.config_id == CollectionConfig.id)
        .where(SourceItem.id.in_(wanted))
    ).all()
    # 管理员代建也只能为商品所属账号引用该账号自己的来源。
    owned = {item.id: item for item, owner_id in rows if owner_id == product.owner_id}
    if len(owned) != len(wanted):
        raise CampaignNotFound("来源条目不存在")
    snapshot_items = [
        {
            "id": owned[item_id].id,
            "platform": owned[item_id].platform,
            "item_kind": owned[item_id].item_kind,
            "source_rank": owned[item_id].source_rank,
            "title": owned[item_id].title,
        }
        for item_id in wanted
    ]
    plan = plan_from_snapshot(
        fact_version_id=fact.id,
        items=snapshot_items,
        target_platforms=target_platforms,
        generation_requirements=generation_requirements,
    )
    campaign = Campaign(
        owner_id=product.owner_id,
        product_id=product.id,
        fact_version_id=fact.id,
        brief=plan["brief"],
        selected_source_item_ids=plan["selected_source_item_ids"],
        status="draft",
    )
    db.add(campaign)
    db.flush()
    run = CampaignRun(
        campaign_id=campaign.id,
        recipe_version="bags-v1",
        generation_budget=generation_budget,
    )
    db.add(run)
    db.flush()
    bound: dict[str, ContentVariant] = {}
    for variant in plan["variants"]:
        row = ContentVariant(
            campaign_id=campaign.id,
            platform=variant["platform"],
            content_type=variant["content_type"],
            version=variant["version"],
            status="draft",
            hashtags=[],
            fact_version_id=fact.id,
        )
        db.add(row)
        bound[variant["platform"]] = row
    db.flush()
    for step in plan["steps"]:
        platform = step.variant_platform or STEP_PLATFORM[step.step_key]
        target = bound.get(platform)
        db.add(
            PipelineStep(
                run_id=run.id,
                step_key=step.step_key,
                variant_platform=platform,
                version=step.version,
                status="pending",
                depends_on=list(STEP_DEPS[step.step_key]),
                output={},
                variant_id=None if target is None else target.id,
            )
        )
    if commit:
        db.commit()
        db.refresh(campaign)
    else:
        db.flush()
    return campaign


def start_campaign(
    db: Session,
    user: User,
    campaign_id: int,
    *,
    idempotency_key: str | None,
    commit: bool = True,
) -> CampaignRun:
    campaign = db.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if campaign is None or not _owns(user, campaign.owner_id):
        raise CampaignNotFound("活动不存在")
    run = db.scalar(
        select(CampaignRun)
        .where(CampaignRun.campaign_id == campaign.id)
        .order_by(CampaignRun.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise CampaignNotFound("活动不存在")
    if run.started_at is not None:
        if commit:
            db.rollback()
        return run
    key = (idempotency_key or "").strip() or None
    if key and run.idempotency_key and run.idempotency_key != key:
        if commit:
            db.rollback()
        return run
    run.idempotency_key = run.idempotency_key or key
    run.started_at = datetime.now(timezone.utc)
    if campaign.status == "draft":
        campaign.status = "generating"
    if commit:
        db.commit()
        db.refresh(run)
    else:
        db.flush()
    return run


def load_campaign(db: Session, user: User, campaign_id: int):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or not _owns(user, campaign.owner_id):
        raise CampaignNotFound("活动不存在")
    run = db.scalar(
        select(CampaignRun)
        .where(CampaignRun.campaign_id == campaign.id)
        .order_by(CampaignRun.id.asc())
    )
    steps = []
    if run is not None:
        steps = list(
            db.scalars(
                select(PipelineStep)
                .where(PipelineStep.run_id == run.id)
                .order_by(PipelineStep.id.asc())
            ).all()
        )
    variants = list(
        db.scalars(
            select(ContentVariant)
            .where(ContentVariant.campaign_id == campaign.id)
            .order_by(ContentVariant.id.asc())
        ).all()
    )
    return campaign, run, steps, variants


def run_count(db: Session, campaign_id: int) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(CampaignRun).where(CampaignRun.campaign_id == campaign_id)
        )
        or 0
    )
