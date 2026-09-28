"""Operator schedules that reuse collection, campaigns and the existing generation worker."""

from __future__ import annotations

import threading
from datetime import date, datetime, time as clock_time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.integrations.sources.base import SourceError
from app.integrations.sources.registry import get_adapter
from app.models import User
from app.models.campaign import CampaignRun
from app.models.operation_plan import OperationPlan, OperationPlanCampaign, OperationPlanDailyUsage, OperationPlanRun
from app.models.product import OwnedProduct, ProductFactVersion
from app.models.source import CollectionConfig, CollectionRun, SourceItem
from app.schemas.operation_plan import PlanFields
from app.services.campaign_service import CampaignNotFound, create_campaign, start_campaign
from app.services.collect import execute_collection, query_from_config
from app.services.consumer_qc import plan_xiaohongshu


WINDOW_GRACE = timedelta(minutes=15)
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 20


class PlanBlocked(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise PlanBlocked("timezone_required", "时间必须包含时区")
    return value.astimezone(timezone.utc)


def _window(day: date, zone_name: str, hhmm: str) -> tuple[datetime, bool]:
    zone = ZoneInfo(zone_name)
    local = datetime.combine(day, clock_time(int(hhmm[:2]), int(hhmm[3:])))
    candidate = local.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
    exists = candidate.astimezone(zone).replace(tzinfo=None) == local
    return candidate, exists


def next_window(after: datetime, zone_name: str, hhmm: str) -> datetime:
    after = _utc(after)
    today = after.astimezone(ZoneInfo(zone_name)).date()
    for offset in range(370):
        candidate, _exists = _window(today + timedelta(days=offset), zone_name, hhmm)
        if candidate > after:
            return candidate
    raise PlanBlocked("schedule_invalid", "无法计算下次执行时间")


def _snapshot(plan: OperationPlan) -> dict:
    return {
        "plan_version": plan.version,
        "timezone": plan.timezone,
        "local_time": plan.local_time,
        "source_config_id": plan.source_config_id,
        "product_ids": list(plan.product_ids or []),
        "target_platforms": list(plan.target_platforms or []),
        "daily_campaign_limit": plan.daily_campaign_limit,
        "daily_budget_limit": plan.daily_budget_limit,
        "generation_budget": plan.generation_budget,
        "auto_advance_to_review": plan.auto_advance_to_review,
    }


def _owned_plan(db: Session, owner_id: int, plan_id: int, *, lock: bool = False) -> OperationPlan:
    query = select(OperationPlan).where(OperationPlan.id == plan_id, OperationPlan.owner_id == owner_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    plan = db.scalar(query)
    if plan is None:
        raise PlanBlocked("plan_missing", "运营计划不存在", 404)
    return plan


def _owned_run(db: Session, owner_id: int, run_id: int) -> tuple[OperationPlan, OperationPlanRun]:
    query = select(OperationPlanRun, OperationPlan).join(
        OperationPlan, OperationPlanRun.plan_id == OperationPlan.id
    ).where(OperationPlanRun.id == run_id, OperationPlan.owner_id == owner_id)
    result = db.execute(query).first()
    if result is None:
        raise PlanBlocked("run_missing", "执行记录不存在", 404)
    return result[1], result[0]


def _validate_scope(db: Session, owner_id: int, fields: PlanFields) -> None:
    products = list(db.scalars(
        select(OwnedProduct).where(OwnedProduct.id.in_(fields.product_ids), OwnedProduct.owner_id == owner_id)
    ))
    if len(products) != len(fields.product_ids):
        raise PlanBlocked("product_missing", "商品范围包含不可用的商品", 404)
    if fields.source_config_id is not None:
        config = db.get(CollectionConfig, fields.source_config_id)
        if config is None or config.owner_id != owner_id:
            raise PlanBlocked("source_missing", "来源配置不存在", 404)


def create_plan(db: Session, user: User, fields: PlanFields) -> OperationPlan:
    _validate_scope(db, user.id, fields)
    now = datetime.now(timezone.utc)
    row = OperationPlan(
        owner_id=user.id,
        **fields.model_dump(),
        version=1,
        next_due_at=next_window(now - timedelta(microseconds=1), fields.timezone, fields.local_time),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def patch_plan(db: Session, user: User, plan_id: int, expected_version: int, changes: dict) -> OperationPlan:
    plan = _owned_plan(db, user.id, plan_id, lock=True)
    if plan.version != expected_version:
        db.rollback()
        raise PlanBlocked("version_changed", "计划已被更新，请刷新", 409)
    data = {**_snapshot(plan), "enabled": plan.enabled}
    data.pop("plan_version")
    data.update(changes)
    fields = PlanFields.model_validate(data)
    _validate_scope(db, user.id, fields)
    old_schedule = (plan.timezone, plan.local_time)
    for key, value in fields.model_dump().items():
        setattr(plan, key, value)
    plan.version += 1
    plan.updated_at = datetime.now(timezone.utc)
    if old_schedule != (plan.timezone, plan.local_time):
        plan.next_due_at = next_window(plan.updated_at, plan.timezone, plan.local_time)
    if not plan.enabled:
        for run in db.scalars(select(OperationPlanRun).where(
            OperationPlanRun.plan_id == plan.id,
            OperationPlanRun.status.in_(("awaiting_selection", "pending_connection", "budget_blocked", "needs_info", "source_empty", "interrupted")),
        ).with_for_update()):
            run.status = "paused"
            run.blocker_code = "plan_paused"
            run.blocker_message = "计划已暂停；已开始的活动仍按原状态机处理"
            run.version += 1
    db.commit()
    db.refresh(plan)
    return plan


def plan_public(plan: OperationPlan) -> dict:
    return {"id": plan.id, "version": plan.version, "enabled": plan.enabled,
            **{key: value for key, value in _snapshot(plan).items() if key != "plan_version"},
            "next_due_at": plan.next_due_at, "created_at": plan.created_at, "updated_at": plan.updated_at}


def run_public(db: Session, run: OperationPlanRun) -> dict:
    snap = dict(run.config_snapshot or {})
    source_meta = dict(run.source_metadata or {})
    products = list(db.scalars(select(OwnedProduct).where(OwnedProduct.id.in_(snap.get("product_ids") or []))))
    candidates = [{"id": item.id, "name": item.name} for item in products]
    next_action = {
        "awaiting_selection": "选择来源参考和自家商品后继续",
        "pending_connection": "连接来源或模型后手动继续",
        "budget_blocked": "调整次日额度或预算后手动继续",
        "needs_info": "补全商品资料后手动继续",
        "source_empty": "检查来源筛选条件后手动继续",
        "interrupted": "核查在途采集后手动继续，避免盲目重提",
        "paused": "启用计划后手动继续",
        "missed": "确认是否补跑这一轮",
        "failed": "人工核查失败原因，不盲目重试外部请求",
    }.get(run.status)
    if run.blocker_code == "generation_budget_empty":
        next_action = "本轮预算固定为 0；调整计划后等待下一轮，或手动创建活动"
    return {
        "id": run.id, "plan_id": run.plan_id, "version": run.version,
        "scheduled_for": run.scheduled_for, "status": run.status,
        "config_snapshot": snap, "source_run_id": run.source_run_id,
        "actual_count": run.actual_count,
        "source_provider": source_meta.get("provider"),
        "source_observed_at": source_meta.get("observed_at"),
        "source_sort_metric": source_meta.get("sort_metric"),
        "source_scope_description": source_meta.get("scope_description"),
        "source_items": list(run.source_items or []),
        "product_candidates": candidates,
        "campaign_ids": list(run.campaign_ids or []),
        "current_daily_campaign_limit": db.get(OperationPlan, run.plan_id).daily_campaign_limit,
        "current_daily_budget_limit": db.get(OperationPlan, run.plan_id).daily_budget_limit,
        "blocker_code": run.blocker_code, "blocker_message": run.blocker_message,
        "next_action": next_action,
        "missed_from": run.missed_from, "missed_count": run.missed_count,
        "created_at": run.created_at, "finished_at": run.finished_at,
    }


def _set_block(run: OperationPlanRun, status: str, code: str, message: str) -> None:
    run.status = status
    run.blocker_code = code
    run.blocker_message = message
    run.claim_token = None
    run.lease_until = None
    run.version += 1


def _claim_lease(run: OperationPlanRun) -> str:
    token = uuid4().hex
    run.claim_token = token
    run.lease_until = datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)
    run.version += 1
    return token


def _claim_is_current(db: Session, run: OperationPlanRun, token: str) -> bool:
    """Call only after locking and refreshing the run row."""
    now = db.scalar(select(func.clock_timestamp()))
    return (run.status == "collecting" and run.claim_token == token
            and run.lease_until is not None and run.lease_until > now)


def _live_claim(db: Session, run_id: int, token: str) -> OperationPlanRun | None:
    try:
        _plan, run = _lock_plan_run(db, run_id)
    except PlanBlocked:
        return None
    return run if _claim_is_current(db, run, token) else None


def renew_lease(run_id: int, token: str) -> bool:
    with SessionLocal() as db:
        run = _live_claim(db, run_id, token)
        if run is None:
            db.rollback()
            return False
        run.lease_until = db.scalar(select(func.clock_timestamp())) + timedelta(seconds=LEASE_SECONDS)
        db.commit()
        return True


def _latest_due(due: datetime, now: datetime, zone_name: str, hhmm: str) -> tuple[datetime, bool, int]:
    zone = ZoneInfo(zone_name)
    due_day = due.astimezone(zone).date()
    now_day = now.astimezone(zone).date()
    candidate, exists = _window(now_day, zone_name, hhmm)
    if candidate > now:
        now_day -= timedelta(days=1)
        candidate, exists = _window(now_day, zone_name, hhmm)
    return candidate, exists, max(1, (now_day - due_day).days + 1)


def claim_due(db: Session, now: datetime) -> tuple[int, str] | None:
    """One short locked transaction claims one due window. A gap yields one missed summary."""
    now = _utc(now)
    plan = db.scalar(
        select(OperationPlan)
        .where(OperationPlan.enabled.is_(True), OperationPlan.next_due_at <= now)
        .order_by(OperationPlan.next_due_at.asc(), OperationPlan.id.asc())
        .limit(1).with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if plan is None:
        db.rollback()
        return None
    due = plan.next_due_at
    latest, exists, count = _latest_due(due, now, plan.timezone, plan.local_time)
    if now > due + WINDOW_GRACE or not exists:
        run = OperationPlanRun(
            plan_id=plan.id, version=1, scheduled_for=latest, status="missed",
            config_snapshot=_snapshot(plan), source_items=[], campaign_ids=[],
            source_metadata={},
            missed_from=due, missed_count=count,
            blocker_code="dst_nonexistent" if not exists else "window_missed",
            blocker_message="夏令时跳过了本地执行时间" if not exists else f"错过 {count} 个调度窗口，未自动补跑",
            finished_at=now,
        )
        token = None
    else:
        run = OperationPlanRun(
            plan_id=plan.id, version=1, scheduled_for=due, status="collecting",
            config_snapshot=_snapshot(plan), source_items=[], campaign_ids=[],
            source_metadata={},
        )
        token = _claim_lease(run)
    db.add(run)
    plan.next_due_at = next_window(now, plan.timezone, plan.local_time)
    db.commit()
    return run.id, token or ""


def recover_expired(db: Session, *, limit: int = 100) -> int:
    """Fence orphaned claims; never restart an external collection automatically."""
    ids = list(db.scalars(
        select(OperationPlanRun.id).where(
            OperationPlanRun.status == "collecting",
            OperationPlanRun.lease_until <= func.clock_timestamp(),
        ).order_by(OperationPlanRun.id.asc()).limit(limit)
    ))
    db.rollback()
    recovered = 0
    for run_id in ids:
        plan, run = _lock_plan_run(db, run_id)
        if run.status == "collecting" and run.lease_until <= db.scalar(select(func.clock_timestamp())):
            _set_block(run, "interrupted", "lease_expired",
                       "上次采集或创建中断；先核查在途结果，再手动继续")
            recovered += 1
        db.commit()
    return recovered


def _source_candidates(db: Session, collection_run_id: int) -> list[dict]:
    rows = list(db.scalars(
        select(SourceItem).where(SourceItem.run_id == collection_run_id)
        .order_by(SourceItem.source_rank.asc(), SourceItem.id.asc()).limit(100)
    ))
    return [{"id": item.id, "source_rank": item.source_rank, "title": item.title,
             "observed_at": item.observed_at.isoformat()} for item in rows]


def _collect_source(run_id: int, token: str, config_id: int) -> None:
    with SessionLocal() as db:
        plan, run = _lock_plan_run(db, run_id)
        owner = db.get(User, plan.owner_id)
        if not _claim_is_current(db, run, token):
            db.rollback()
            return
        if not plan.enabled or owner is None or not owner.is_active or owner.is_frozen:
            _set_block(run, "paused", "owner_unavailable" if owner is None or not owner.is_active or owner.is_frozen else "plan_paused",
                       "账号不可执行或计划暂停，未发起新的来源请求")
            db.commit()
            return
        db.rollback()
    with SessionLocal() as db:
        config = db.get(CollectionConfig, config_id)
        if config is None or not config.enabled:
            _finish_block(run_id, token, "pending_connection", "source_pending", "来源配置待连接或已停用")
            return
        try:
            get_adapter(config.provider).ensure_ready(query_from_config(config))
        except SourceError:
            _finish_block(run_id, token, "pending_connection", "source_pending", "来源未连接或缺少采集能力")
            return
        config_meta = {"provider": config.provider, "sort_metric": config.sort_metric, "window": config.window}
        db.rollback()

    stop = threading.Event()
    lost = threading.Event()

    def beat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                ok = renew_lease(run_id, token)
            except Exception:
                ok = False
            if not ok:
                lost.set()
                return

    heartbeat = threading.Thread(target=beat, name="plan-collection-heartbeat", daemon=True)
    heartbeat.start()
    try:
        with SessionLocal() as collection_db:
            config = collection_db.get(CollectionConfig, config_id)
            collected = execute_collection(collection_db, config)
            collection_run_id = collected.id
    finally:
        stop.set()
        heartbeat.join(timeout=5)
    if lost.is_set():
        return
    with SessionLocal() as db:
        run = _live_claim(db, run_id, token)
        if run is None:
            db.rollback()
            return
        plan = db.get(OperationPlan, run.plan_id)
        collection = db.get(CollectionRun, collection_run_id)
        run.source_run_id = collection.id
        run.actual_count = collection.actual_count
        run.source_items = _source_candidates(db, collection.id)
        run.source_metadata = {
            **config_meta,
            "scope_description": collection.scope_description,
            "observed_at": collection.finished_at.isoformat() if collection.finished_at else None,
        }
        if not plan.enabled:
            _set_block(run, "paused", "plan_paused", "采集已完成，但计划暂停，未创建活动")
        elif collection.status == "failed":
            _set_block(run, "pending_connection", "source_failed", "来源采集失败；检查连接后手动继续")
        elif not run.source_items:
            _set_block(run, "source_empty", "source_empty", "本轮实际取得 0 条来源；检查来源后手动继续")
        else:
            _set_block(run, "awaiting_selection", "association_required", "请选择来源参考与自家商品的对应关系")
        db.commit()


def _finish_block(run_id: int, token: str, status: str, code: str, message: str) -> None:
    with SessionLocal() as db:
        run = _live_claim(db, run_id, token)
        if run is None:
            db.rollback()
            return
        _set_block(run, status, code, message)
        db.commit()


def _usage(db: Session, plan_id: int, local_date: date) -> OperationPlanDailyUsage:
    db.execute(pg_insert(OperationPlanDailyUsage).values(
        plan_id=plan_id, local_date=local_date, campaign_count=0, budget_reserved=0,
    ).on_conflict_do_nothing(index_elements=["plan_id", "local_date"]))
    return db.scalar(
        select(OperationPlanDailyUsage)
        .where(OperationPlanDailyUsage.plan_id == plan_id, OperationPlanDailyUsage.local_date == local_date)
        .with_for_update().execution_options(populate_existing=True)
    )


def _current_fact(db: Session, product_id: int) -> ProductFactVersion | None:
    return db.scalar(
        select(ProductFactVersion).where(ProductFactVersion.product_id == product_id)
        .order_by(ProductFactVersion.version.desc()).limit(1)
    )


def _lock_plan_run(db: Session, run_id: int) -> tuple[OperationPlan, OperationPlanRun]:
    plan_id = db.scalar(select(OperationPlanRun.plan_id).where(OperationPlanRun.id == run_id))
    if plan_id is None:
        raise PlanBlocked("run_missing", "执行记录不存在", 404)
    db.execute(text("SET LOCAL lock_timeout = '8s'"))
    plan = db.scalar(
        select(OperationPlan).where(OperationPlan.id == plan_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    run = db.scalar(
        select(OperationPlanRun).where(OperationPlanRun.id == run_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    return plan, run


def _create_one(db: Session, plan: OperationPlan, run: OperationPlanRun, product_id: int,
                source_item_id: int | None) -> int | None:
    snap = dict(run.config_snapshot or {})
    existing = db.scalar(select(OperationPlanCampaign).where(
        OperationPlanCampaign.plan_run_id == run.id,
        OperationPlanCampaign.product_id == product_id,
        OperationPlanCampaign.source_item_id == source_item_id,
    ))
    if existing is not None:
        return existing.campaign_id
    owner = db.get(User, plan.owner_id)
    if owner is None or not owner.is_active or owner.is_frozen:
        _set_block(run, "paused", "owner_unavailable", "账号已停用或冻结，停止创建新活动")
        return None
    if not plan.enabled:
        _set_block(run, "paused", "plan_paused", "计划已暂停，未创建新活动")
        return None
    if snap.get("auto_advance_to_review") and not settings.ARK_API_KEY:
        _set_block(run, "pending_connection", "model_pending", "模型尚未连接，未创建活动")
        return None
    product = db.get(OwnedProduct, product_id)
    fact = _current_fact(db, product_id)
    if product is None or product.owner_id != plan.owner_id or not product.active or product.primary_asset_id is None or fact is None:
        _set_block(run, "needs_info", "product_incomplete", "商品资料或主图缺失，先补全再继续")
        return None
    plan_for_facts = plan_xiaohongshu(dict(fact.facts or {}), title=product.name, body="")
    if plan_for_facts.get("code") is not None:
        _set_block(run, "needs_info", "facts_missing", "商品缺少至少一项已确认事实，先补全再继续")
        return None
    now = datetime.now(timezone.utc)
    usage = _usage(db, plan.id, now.astimezone(ZoneInfo(snap["timezone"])).date())
    reserve = int(snap["generation_budget"])
    if snap.get("auto_advance_to_review") and reserve == 0:
        _set_block(run, "budget_blocked", "generation_budget_empty", "本轮自动生成预算为 0，不能启动生成")
        return None
    if usage.campaign_count >= plan.daily_campaign_limit or usage.budget_reserved + reserve > plan.daily_budget_limit:
        _set_block(run, "budget_blocked", "daily_limit", "当前计划当地日期的活动或调用额度已满")
        return None
    try:
        campaign = create_campaign(
            db, owner, product_id=product.id, fact_version_id=fact.id,
            source_item_ids=[] if source_item_id is None else [source_item_id],
            target_platforms=list(snap["target_platforms"]),
            generation_budget=reserve, commit=False,
        )
        if snap.get("auto_advance_to_review"):
            start_campaign(db, owner, campaign.id,
                           idempotency_key=f"plan:{run.id}:product:{product.id}:source:{source_item_id or 0}",
                           commit=False)
    except CampaignNotFound:
        _set_block(run, "needs_info", "scope_changed", "来源或商品事实版本已变化，请核查后继续")
        return None
    db.add(OperationPlanCampaign(
        plan_run_id=run.id, campaign_id=campaign.id, product_id=product.id, source_item_id=source_item_id,
    ))
    usage.campaign_count += 1
    usage.budget_reserved += reserve
    run.campaign_ids = [*list(run.campaign_ids or []), campaign.id]
    run.version += 1
    run.blocker_code = None
    run.blocker_message = None
    return campaign.id


def _advance_no_source(run_id: int, token: str) -> None:
    with SessionLocal() as db:
        run = _live_claim(db, run_id, token)
        if run is None:
            db.rollback()
            return
        product_ids = list((run.config_snapshot or {}).get("product_ids") or [])
        db.rollback()
    for product_id in product_ids:
        if not renew_lease(run_id, token):
            return
        with SessionLocal() as db:
            plan, run = _lock_plan_run(db, run_id)
            if not _claim_is_current(db, run, token):
                db.rollback()
                return
            created = _create_one(db, plan, run, product_id, None)
            if created is None:
                db.commit()
                return
            db.commit()
    with SessionLocal() as db:
        plan, run = _lock_plan_run(db, run_id)
        if not _claim_is_current(db, run, token):
            db.rollback()
            return
        if not plan.enabled:
            _set_block(run, "paused", "plan_paused", "计划已暂停；已有活动继续按原状态机处理")
        else:
            run.status = "created"
            run.blocker_code = None
            run.blocker_message = None
            run.claim_token = None
            run.lease_until = None
            run.finished_at = datetime.now(timezone.utc)
            run.version += 1
        db.commit()


def process_run(run_id: int, token: str) -> None:
    with SessionLocal() as db:
        run = _live_claim(db, run_id, token)
        if run is None:
            db.rollback()
            return
        snap = dict(run.config_snapshot or {})
        plan = db.get(OperationPlan, run.plan_id)
        owner = db.get(User, plan.owner_id)
        if owner is None or not owner.is_active or owner.is_frozen or not plan.enabled:
            _set_block(run, "paused", "owner_unavailable" if owner is None or not owner.is_active or owner.is_frozen else "plan_paused",
                       "账号不可执行或计划已暂停，未启动新任务")
            db.commit()
            return
        db.rollback()
    if snap.get("source_config_id") is not None:
        _collect_source(run_id, token, int(snap["source_config_id"]))
    else:
        _advance_no_source(run_id, token)


def tick(db: Session, *, now: datetime | None = None, limit: int = 10) -> list[int]:
    """Finite scheduler pass. Processed rounds are never approved or published here."""
    when = _utc(now or datetime.now(timezone.utc))
    recover_expired(db)
    ids: list[int] = []
    for _ in range(limit):
        claim = claim_due(db, when)
        if claim is None:
            break
        run_id, token = claim
        ids.append(run_id)
        if token:
            process_run(run_id, token)
    return ids


def backfill(db: Session, user: User, plan_id: int, expected_version: int,
             scheduled_for: datetime) -> OperationPlanRun:
    plan = _owned_plan(db, user.id, plan_id, lock=True)
    if plan.version != expected_version:
        db.rollback()
        raise PlanBlocked("version_changed", "计划已变化，请刷新", 409)
    if not plan.enabled:
        db.rollback()
        raise PlanBlocked("plan_paused", "计划已暂停，先启用再补跑", 409)
    run = db.scalar(select(OperationPlanRun).where(
        OperationPlanRun.plan_id == plan.id,
        OperationPlanRun.scheduled_for == _utc(scheduled_for),
    ).with_for_update().execution_options(populate_existing=True))
    if run is None:
        db.rollback()
        raise PlanBlocked("window_missing", "没有这次错过的窗口", 404)
    if run.status != "missed":
        db.rollback()
        return run
    run.status = "collecting"
    run.blocker_code = None
    run.blocker_message = None
    run.finished_at = None
    token = _claim_lease(run)
    db.commit()
    process_run(run.id, token)
    db.expire_all()
    return db.get(OperationPlanRun, run.id)


def resume_run(db: Session, user: User, run_id: int, expected_version: int) -> OperationPlanRun:
    plan, run = _lock_plan_run(db, run_id)
    if plan.owner_id != user.id:
        db.rollback()
        raise PlanBlocked("run_missing", "执行记录不存在", 404)
    if run.version != expected_version:
        db.rollback()
        raise PlanBlocked("version_changed", "执行记录已变化，请刷新", 409)
    if run.status not in {"pending_connection", "budget_blocked", "paused", "needs_info", "source_empty", "interrupted"}:
        db.rollback()
        raise PlanBlocked("resume_unavailable", "这一轮不能直接继续", 409)
    if not plan.enabled:
        db.rollback()
        raise PlanBlocked("plan_paused", "计划已暂停，先启用再继续", 409)
    if run.source_items:
        run.status = "awaiting_selection"
        run.blocker_code = "association_required"
        run.blocker_message = "请选择来源参考与自家商品的对应关系"
        run.version += 1
        db.commit()
    else:
        run.status = "collecting"
        run.blocker_code = None
        run.blocker_message = None
        token = _claim_lease(run)
        db.commit()
        process_run(run.id, token)
    db.expire_all()
    return db.get(OperationPlanRun, run.id)


def resolve_run(db: Session, user: User, run_id: int, expected_version: int,
                source_item_id: int, product_id: int) -> OperationPlanRun:
    plan, run = _lock_plan_run(db, run_id)
    if plan.owner_id != user.id:
        db.rollback()
        raise PlanBlocked("run_missing", "执行记录不存在", 404)
    if run.version != expected_version:
        db.rollback()
        raise PlanBlocked("version_changed", "执行记录已变化，请刷新", 409)
    if run.status != "awaiting_selection" or not plan.enabled:
        db.rollback()
        raise PlanBlocked("selection_unavailable", "这一轮目前不能关联商品", 409)
    snap = dict(run.config_snapshot or {})
    if product_id not in snap.get("product_ids", []) or source_item_id not in [item["id"] for item in run.source_items or []]:
        db.rollback()
        raise PlanBlocked("choice_invalid", "来源或商品不属于这一轮候选", 422)
    source = db.get(SourceItem, source_item_id)
    if source is None or source.run_id != run.source_run_id:
        db.rollback()
        raise PlanBlocked("source_missing", "来源条目已不可用", 422)
    created = _create_one(db, plan, run, product_id, source_item_id)
    if created is not None:
        run.status = "awaiting_selection"
    db.commit()
    db.expire_all()
    return db.get(OperationPlanRun, run.id)
