"""A plan creates one auditable round per window; it never auto-approves or publishes."""

from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from multiprocessing import Manager, get_context
import os
import sys
import threading

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models.campaign import Campaign, CampaignRun
from app.models.operation_plan import OperationPlan, OperationPlanCampaign, OperationPlanDailyUsage, OperationPlanRun
from app.models.source import CollectionConfig, CollectionRun
from app.integrations.sources.base import CredentialsMissing, SourcePage
from app.services import collect, operation_plans
from app.services.operation_plans import _window, tick
from tests.conftest import as_user, cleanup
from tests.test_campaign_db import _seed


def _plan_body(product_id, *, source_config_id=None, auto=True):
    return {
        "enabled": True,
        "timezone": "Asia/Shanghai",
        "local_time": "09:30",
        "source_config_id": source_config_id,
        "product_ids": [product_id],
        "target_platforms": ["douyin"],
        "daily_campaign_limit": 2,
        "daily_budget_limit": 8,
        "generation_budget": 3,
        "auto_advance_to_review": auto,
    }


def test_create_list_and_patch_plan_with_version(client, db):
    user, product, _fact, _first, _second = _seed(db)
    as_user(user)
    try:
        created = client.post("/api/operation-plans", json=_plan_body(product.id))
        assert created.status_code == 200, created.text
        plan = created.json()
        assert plan["version"] == 1
        assert plan["product_ids"] == [product.id]
        assert plan["source_config_id"] is None
        assert plan["next_due_at"] is not None
        listed = client.get("/api/operation-plans")
        assert listed.status_code == 200
        assert any(item["id"] == plan["id"] for item in listed.json()["items"])
        paused = client.patch(
            f"/api/operation-plans/{plan['id']}",
            json={"expected_version": 1, "enabled": False},
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["version"] == 2
        stale = client.patch(
            f"/api/operation-plans/{plan['id']}",
            json={"expected_version": 1, "enabled": True},
        )
        assert stale.status_code == 409
    finally:
        cleanup(db, user)


def _due(db, plan_id):
    plan = db.get(OperationPlan, plan_id)
    plan.next_due_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    due = plan.next_due_at
    db.commit()
    return due


def test_no_source_round_creates_draft_once_and_reserves_budget(client, db):
    user, product, _fact, _first, _second = _seed(db)
    as_user(user)
    try:
        response = client.post("/api/operation-plans", json=_plan_body(product.id, auto=False))
        assert response.status_code == 200, response.text
        plan_id = response.json()["id"]
        due = _due(db, plan_id)
        assert len(tick(db, now=due + timedelta(seconds=1))) == 1
        assert tick(db, now=due + timedelta(seconds=1)) == []
        run = db.scalar(select(OperationPlanRun).where(OperationPlanRun.plan_id == plan_id))
        db.refresh(run)
        assert run.status == "created"
        assert len(run.campaign_ids) == 1
        campaign = db.get(Campaign, run.campaign_ids[0])
        assert campaign.status == "draft"
        assert campaign.selected_source_item_ids == []
        assert campaign.brief["target_platforms"] == ["douyin"]
        generation = db.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign.id))
        assert generation.started_at is None
        usage = db.scalar(select(OperationPlanDailyUsage).where(OperationPlanDailyUsage.plan_id == plan_id))
        assert usage.campaign_count == 1 and usage.budget_reserved == 3
    finally:
        cleanup(db, user)


def test_missing_model_stops_before_activity_and_resume_is_explicit(client, db, monkeypatch):
    from app.core.config import settings
    user, product, _fact, _first, _second = _seed(db)
    as_user(user)
    try:
        plan_id = client.post("/api/operation-plans", json=_plan_body(product.id)).json()["id"]
        due = _due(db, plan_id)
        monkeypatch.setattr(settings, "ARK_API_KEY", "")
        tick(db, now=due + timedelta(seconds=1))
        run = db.scalar(select(OperationPlanRun).where(OperationPlanRun.plan_id == plan_id))
        db.refresh(run)
        assert run.status == "pending_connection"
        assert run.blocker_code == "model_pending"
        assert run.campaign_ids == []
        assert db.scalar(select(func.count()).select_from(OperationPlanCampaign).where(
            OperationPlanCampaign.plan_run_id == run.id)) == 0
    finally:
        cleanup(db, user)


def test_missed_window_requires_backfill_and_keeps_old_snapshot(client, db):
    user, product, _fact, _first, _second = _seed(db)
    as_user(user)
    try:
        plan = client.post("/api/operation-plans", json=_plan_body(product.id, auto=False)).json()
        row = db.get(OperationPlan, plan["id"])
        row.next_due_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.commit()
        assert len(tick(db)) == 1
        run = db.scalar(select(OperationPlanRun).where(OperationPlanRun.plan_id == plan["id"]))
        db.refresh(run)
        assert run.status == "missed"
        assert run.missed_count >= 2
        assert run.campaign_ids == []
        assert tick(db) == []
        paused = client.patch(f"/api/operation-plans/{plan['id']}",
                              json={"expected_version": 1, "target_platforms": ["xiaohongshu"]})
        assert paused.status_code == 200
        result = client.post(f"/api/operation-plans/{plan['id']}/backfill", json={
            "expected_version": 2, "scheduled_for": run.scheduled_for.isoformat(),
        })
        assert result.status_code == 200, result.text
        assert result.json()["id"] == run.id
        assert result.json()["config_snapshot"]["target_platforms"] == ["douyin"]
        assert len(result.json()["campaign_ids"]) == 1
    finally:
        cleanup(db, user)


def test_dst_nonexistent_and_repeated_local_windows():
    missing, exists = _window(datetime(2026, 3, 8).date(), "America/New_York", "02:30")
    assert not exists
    assert missing.tzinfo == timezone.utc
    first, exists = _window(datetime(2026, 11, 1).date(), "America/New_York", "01:30")
    assert exists
    assert first.hour == 5  # fold=0: the repeated local time runs once at the first instant.


def _process_tick(now_iso):
    from app.core.db import SessionLocal
    from app.services.operation_plans import tick
    with SessionLocal() as session:
        return os.getpid(), tick(session, now=datetime.fromisoformat(now_iso))


def _process_tick_holding_plan(now_iso, acquired, release):
    from app.core.db import SessionLocal
    from app.models.operation_plan import OperationPlan
    from app.services.operation_plans import tick
    with SessionLocal() as session:
        scalar = session.scalar
        held = False

        def hold_after_lock(statement, *args, **kwargs):
            nonlocal held
            row = scalar(statement, *args, **kwargs)
            if isinstance(row, OperationPlan) and not held:
                held = True
                acquired.set()
                assert release.wait(15)
            return row

        session.scalar = hold_after_lock
        return os.getpid(), tick(session, now=datetime.fromisoformat(now_iso))


def test_two_scheduler_processes_create_only_one_window(client, db):
    user, product, _fact, _first, _second = _seed(db)
    as_user(user)
    try:
        plan = client.post("/api/operation-plans", json=_plan_body(product.id, auto=False)).json()
        due = _due(db, plan["id"])
        stamp = (due + timedelta(seconds=1)).isoformat()
        with Manager() as manager, ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as executor:
            acquired, release = manager.Event(), manager.Event()
            first_job = executor.submit(_process_tick_holding_plan, stamp, acquired, release)
            assert acquired.wait(15)
            competing = executor.submit(_process_tick, stamp)
            second = competing.result(timeout=15)
            release.set()
            first = first_job.result(timeout=15)
        assert first[0] != second[0]
        assert len(first[1]) == 1 and second[1] == []
        rows = list(db.scalars(select(OperationPlanRun).where(OperationPlanRun.plan_id == plan["id"])))
        assert len(rows) == 1
        assert len(db.scalars(select(OperationPlanCampaign).where(OperationPlanCampaign.plan_run_id == rows[0].id)).all()) == 1
    finally:
        cleanup(db, user)


def test_budget_limit_blocks_then_explicit_resume_uses_new_daily_limit(client, db):
    user, product, _fact, _first, _second = _seed(db)
    as_user(user)
    try:
        body = _plan_body(product.id, auto=False)
        body["daily_budget_limit"] = 0
        plan = client.post("/api/operation-plans", json=body).json()
        due = _due(db, plan["id"])
        tick(db, now=due + timedelta(seconds=1))
        run = db.scalar(select(OperationPlanRun).where(OperationPlanRun.plan_id == plan["id"]))
        db.refresh(run)
        assert run.status == "budget_blocked" and run.campaign_ids == []
        changed = client.patch(f"/api/operation-plans/{plan['id']}",
                               json={"expected_version": 1, "daily_budget_limit": 3})
        assert changed.status_code == 200
        resumed = client.post(f"/api/operation-plans/runs/{run.id}/resume", json={"expected_version": run.version})
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "created"
        assert len(resumed.json()["campaign_ids"]) == 1
        assert resumed.json()["config_snapshot"]["daily_budget_limit"] == 0
        assert resumed.json()["current_daily_budget_limit"] == 3
    finally:
        cleanup(db, user)


def test_zero_generation_budget_does_not_start_automatic_work(client, db):
    user, product, _fact, _first, _second = _seed(db)
    as_user(user)
    try:
        body = _plan_body(product.id)
        body["generation_budget"] = 0
        plan_id = client.post("/api/operation-plans", json=body).json()["id"]
        due = _due(db, plan_id)
        tick(db, now=due + timedelta(seconds=1))
        run = db.scalar(select(OperationPlanRun).where(OperationPlanRun.plan_id == plan_id))
        db.refresh(run)
        assert run.status == "budget_blocked"
        assert run.blocker_code == "generation_budget_empty"
        assert run.campaign_ids == []
    finally:
        cleanup(db, user)


def test_scheduler_cli_modes_are_explicit(monkeypatch):
    from app import operation_scheduler

    calls = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_tick(_session, *, now):
        calls.append(now)
        return []

    monkeypatch.setattr(operation_scheduler, "SessionLocal", FakeSession)
    monkeypatch.setattr(operation_scheduler, "tick", fake_tick)
    monkeypatch.setattr(sys, "argv", ["operation_scheduler", "--once"])
    operation_scheduler.main()
    assert len(calls) == 1

    def stop_after_first_wait(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(operation_scheduler.time, "sleep", stop_after_first_wait)
    monkeypatch.setattr(sys, "argv", ["operation_scheduler", "--loop", "--poll-seconds", "1"])
    operation_scheduler.main()
    assert len(calls) == 2


def test_source_unavailable_records_pending_without_fetch(client, db, monkeypatch):
    user, product, _fact, first, _second = _seed(db)
    as_user(user)
    fetched = []

    class Missing:
        def ensure_ready(self, _query):
            raise CredentialsMissing("unavailable")

        def fetch_page(self, *_args):
            fetched.append(True)
            raise AssertionError("must not fetch")

    monkeypatch.setattr(operation_plans, "get_adapter", lambda _provider: Missing())
    try:
        config_id = db.get(CollectionRun, first.run_id).config_id
        plan = client.post("/api/operation-plans", json=_plan_body(product.id, source_config_id=config_id)).json()
        due = _due(db, plan["id"])
        tick(db, now=due + timedelta(seconds=1))
        run = db.scalar(select(OperationPlanRun).where(OperationPlanRun.plan_id == plan["id"]))
        db.refresh(run)
        assert run.status == "pending_connection"
        assert run.source_run_id is None and run.campaign_ids == []
        assert fetched == []
    finally:
        cleanup(db, user)


def test_collector_has_no_open_transaction_during_fetch(db, monkeypatch):
    user, _product, _fact, first, _second = _seed(db)
    try:
        config = db.get(CollectionConfig, db.get(CollectionRun, first.run_id).config_id)

        class LocalAdapter:
            def ensure_ready(self, _query):
                pass

            def fetch_page(self, _query, _cursor):
                assert not db.in_transaction()
                return SourcePage(items=[], next_cursor=None, scope_description="隔离空样本")

        monkeypatch.setattr(collect, "get_adapter", lambda _provider: LocalAdapter())
        result = collect.execute_collection(db, config)
        assert result.status == "succeeded" and result.actual_count == 0
    finally:
        cleanup(db, user)


def test_pause_during_source_io_records_result_but_creates_no_campaign(client, db, monkeypatch):
    user, product, _fact, first, _second = _seed(db)
    as_user(user)
    entered = threading.Event()
    release = threading.Event()
    results = []

    class Ready:
        def ensure_ready(self, _query):
            pass

    def paused_collection(session, config):
        entered.set()
        assert release.wait(10)
        result = CollectionRun(config_id=config.id, status="succeeded", actual_count=0,
                               scope_description="隔离空样本", started_at=datetime.now(timezone.utc),
                               finished_at=datetime.now(timezone.utc))
        session.add(result)
        session.commit()
        return result

    monkeypatch.setattr(operation_plans, "get_adapter", lambda _provider: Ready())
    monkeypatch.setattr(operation_plans, "execute_collection", paused_collection)
    try:
        config_id = db.get(CollectionRun, first.run_id).config_id
        plan = client.post("/api/operation-plans", json=_plan_body(product.id, source_config_id=config_id)).json()
        due = _due(db, plan["id"])

        def run_tick():
            with SessionLocal() as session:
                results.extend(tick(session, now=due + timedelta(seconds=1)))

        thread = threading.Thread(target=run_tick)
        thread.start()
        assert entered.wait(10)
        paused = client.patch(f"/api/operation-plans/{plan['id']}",
                              json={"expected_version": 1, "enabled": False})
        assert paused.status_code == 200, paused.text
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        run = db.scalar(select(OperationPlanRun).where(OperationPlanRun.plan_id == plan["id"]))
        db.refresh(run)
        assert run.status == "paused"
        assert run.source_run_id is not None
        assert run.campaign_ids == []
    finally:
        release.set()
        cleanup(db, user)


def test_source_candidates_wait_for_explicit_product_binding(client, db, monkeypatch):
    user, product, _fact, first, _second = _seed(db)
    as_user(user)

    class Ready:
        def ensure_ready(self, _query):
            pass

    monkeypatch.setattr(operation_plans, "get_adapter", lambda _provider: Ready())
    monkeypatch.setattr(operation_plans, "execute_collection",
                        lambda session, _config: session.get(CollectionRun, first.run_id))
    try:
        config_id = db.get(CollectionRun, first.run_id).config_id
        plan = client.post("/api/operation-plans", json=_plan_body(product.id, source_config_id=config_id, auto=False)).json()
        due = _due(db, plan["id"])
        tick(db, now=due + timedelta(seconds=1))
        run = db.scalar(select(OperationPlanRun).where(OperationPlanRun.plan_id == plan["id"]))
        db.refresh(run)
        assert run.status == "awaiting_selection"
        assert run.actual_count == 2
        assert [item["id"] for item in run.source_items][:1] == [first.id]
        assert run.campaign_ids == []
        invalid = client.post(f"/api/operation-plans/runs/{run.id}/resolve", json={
            "expected_version": run.version, "source_item_id": first.id, "product_id": product.id + 100000,
        })
        assert invalid.status_code == 422
        resolved = client.post(f"/api/operation-plans/runs/{run.id}/resolve", json={
            "expected_version": run.version, "source_item_id": first.id, "product_id": product.id,
        })
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["status"] == "awaiting_selection"
        assert len(resolved.json()["campaign_ids"]) == 1
        campaign = db.get(Campaign, resolved.json()["campaign_ids"][0])
        assert campaign.selected_source_item_ids == [first.id]
        assert campaign.product_id == product.id
        again = client.post(f"/api/operation-plans/runs/{run.id}/resolve", json={
            "expected_version": resolved.json()["version"], "source_item_id": first.id, "product_id": product.id,
        })
        assert again.status_code == 200
        assert again.json()["campaign_ids"] == resolved.json()["campaign_ids"]
    finally:
        cleanup(db, user)


def test_expired_claim_is_interrupted_not_automatically_replayed(client, db):
    user, product, _fact, _first, _second = _seed(db)
    as_user(user)
    try:
        plan = client.post("/api/operation-plans", json=_plan_body(product.id, auto=False)).json()
        due = _due(db, plan["id"])
        claim = operation_plans.claim_due(db, due + timedelta(seconds=1))
        run = db.get(OperationPlanRun, claim[0])
        run.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert tick(db, now=due + timedelta(seconds=1)) == []
        db.refresh(run)
        assert run.status == "interrupted" and run.campaign_ids == []
        resumed = client.post(f"/api/operation-plans/runs/{run.id}/resume",
                              json={"expected_version": run.version})
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "created"
        assert len(resumed.json()["campaign_ids"]) == 1
    finally:
        cleanup(db, user)
