"""运营总览。每个当前平台变体只落入一个统计桶。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.campaign import Campaign, CampaignRun, ContentVariant, PipelineStep
from app.models.product import OwnedProduct, ProductFactVersion
from app.models.publish import PublishJob
from app.models.user import User
from app.services.publish_jobs import content_blockers

BUCKETS = (
    "needs_info", "pending_generation", "generating", "needs_review",
    "approved_ready", "publication_exception",
)
PUBLISH_EXCEPTIONS = {"failed", "publish_unknown", "needs_reconfirm"}


def _current_variants(db: Session, campaign_id: int) -> list[ContentVariant]:
    rows = list(db.scalars(
        select(ContentVariant).where(ContentVariant.campaign_id == campaign_id)
        .order_by(ContentVariant.platform, ContentVariant.version.desc(), ContentVariant.id.desc())
    ).all())
    latest = {}
    for row in rows:
        latest.setdefault(row.platform, row)
    return list(latest.values())


def _latest_steps(db: Session, run_id: int, platform: str) -> list[PipelineStep]:
    rows = list(db.scalars(
        select(PipelineStep).where(
            PipelineStep.run_id == run_id,
            PipelineStep.variant_platform.in_(("", platform)),
        ).order_by(PipelineStep.version.desc(), PipelineStep.id.desc())
    ).all())
    latest = {}
    for row in rows:
        latest.setdefault(row.step_key, row)
    return list(latest.values())


def _item(
    db: Session, campaign: Campaign, variant: ContentVariant, run: CampaignRun | None,
    product: OwnedProduct | None, fact: ProductFactVersion | None,
) -> dict:
    steps = _latest_steps(db, run.id, variant.platform) if run else []
    jobs = list(db.scalars(
        select(PublishJob).where(
            PublishJob.campaign_id == campaign.id,
            PublishJob.platform == variant.platform,
        ).order_by(PublishJob.id.desc())
    ).all())
    # 旧版的明确失败已被新版本覆盖；结果未知仍要人工核验。
    unresolved_unknown = next((job for job in jobs if job.status == "publish_unknown"), None)
    current_job = next((job for job in jobs if job.variant_id == variant.id), None)
    exception_job = unresolved_unknown or (
        current_job if current_job and current_job.status in PUBLISH_EXCEPTIONS else None
    )
    blockers: list[dict] = []
    error = next((step for step in steps if step.status in {"failed", "unknown", "skipped"}), None)
    if exception_job:
        job = exception_job
        bucket = "publication_exception"
        blockers.append({
            "code": job.error_code or job.status,
            "message": "发布结果未知，需人工核对" if job.status == "publish_unknown" else "发布任务需要处理",
        })
        action = "打开发布任务，核对平台状态或重新确认排期"
    elif variant.status != "approved" and (product is None or product.primary_asset_id is None or fact is None):
        bucket = "needs_info"
        blockers.append({"code": "product_asset_missing", "message": "请补充自家商品主图和事实版本"})
        action = "补充商品资料"
    elif run is None or run.started_at is None:
        bucket = "pending_generation"
        action = "确认并启动生成"
    elif error is not None:
        bucket = "pending_generation"
        code = error.error_code or error.status
        message = "生成结果未知，请联系运维按活动详情的请求编号核查，不要直接重做" if error.status == "unknown" else "生成步骤未完成，请检查原因"
        blockers.append({"code": code, "message": message})
        action = "联系运维核查请求编号" if error.status == "unknown" else "查看失败步骤并处理"
    elif variant.status == "approved":
        content = content_blockers(db, campaign, variant, check_storage=False)
        if content:
            bucket = "needs_review"
            blockers.extend(content)
            action = "核对当前版本与审核快照"
        else:
            bucket = "approved_ready"
            action = "导出已审素材或安排发布"
    elif any(step.status in {"pending", "running"} for step in steps):
        if not settings.ARK_API_KEY and any(
            step.step_key.startswith("copy:") and step.status == "pending" for step in steps
        ):
            bucket = "pending_generation"
            blockers.append({"code": "model_pending_connection", "message": "生成模型待连接"})
            action = "配置生成模型后继续"
        else:
            bucket = "generating"
            action = "查看生成进度"
    else:
        bucket = "needs_review"
        action = "打开平台内容并审核"

    return {
        "campaign_id": campaign.id,
        "platform": variant.platform,
        "version": variant.version,
        "bucket": bucket,
        "status": variant.status,
        "product_id": campaign.product_id,
        "product_name": product.name if product else "",
        "budget_reserved": run.budget_reserved if run else 0,
        "generation_budget": run.generation_budget if run else 0,
        "generation_connection": "configured" if settings.ARK_API_KEY else "pending_connection",
        "blockers": blockers,
        "next_action": action,
        "publish_job_id": exception_job.id if exception_job else None,
    }


def overview(db: Session, user: User, *, bucket: str | None = None) -> dict:
    if bucket is not None and bucket not in BUCKETS:
        raise ValueError("无效的运营分类")
    query = select(Campaign).order_by(Campaign.id.desc())
    if user.role != "admin":
        query = query.where(Campaign.owner_id == user.id)
    items = []
    for campaign in db.scalars(query).all():
        run = db.scalar(
            select(CampaignRun).where(CampaignRun.campaign_id == campaign.id).order_by(CampaignRun.id.asc())
        )
        product = db.get(OwnedProduct, campaign.product_id)
        fact = db.get(ProductFactVersion, campaign.fact_version_id)
        for variant in _current_variants(db, campaign.id):
            items.append(_item(db, campaign, variant, run, product, fact))
    counts = {name: sum(item["bucket"] == name for item in items) for name in BUCKETS}
    return {
        "dimension": "platform_variant",
        "counts": counts,
        "items": [item for item in items if bucket is None or item["bucket"] == bucket],
    }
