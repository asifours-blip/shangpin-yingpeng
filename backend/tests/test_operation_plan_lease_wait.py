"""A lease must be checked after PostgreSQL grants a contended run-row lock."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time
from threading import Event

from sqlalchemy import func, select, text

from app.core.db import SessionLocal
from app.integrations.sources.base import CredentialsMissing, SourceItemIn, SourcePage
from app.models.operation_plan import OperationPlanCampaign, OperationPlanRun
from app.models.source import CollectionRun
from app.services import collect, operation_plans
from tests.conftest import as_user, cleanup
from tests.test_campaign_db import _seed
from tests.test_operation_plans import _due, _plan_body


def _claimed_source_round(client, db):
    user, product, _fact, first, _second = _seed(db)
    as_user(user)
    config_id = db.get(CollectionRun, first.run_id).config_id
    plan = client.post("/api/operation-plans", json=_plan_body(product.id, source_config_id=config_id))
    assert plan.status_code == 200, plan.text
    due = _due(db, plan.json()["id"])
    claimed = operation_plans.claim_due(db, due + timedelta(seconds=1))
    assert claimed is not None and claimed[1]
    run_id, token = claimed
    db.execute(text("""
        UPDATE operation_plan_runs
        SET lease_until = clock_timestamp() + interval '4 seconds'
        WHERE id = :run_id
    """), {"run_id": run_id})
    db.commit()
    return user, first, config_id, run_id, token


def _wait_for_db_lock(run_id: int, lease_deadline: datetime, blocker_pid: int):
    """Observe a real backend waiting on a PostgreSQL lock before the lease ends."""
    until = time.monotonic() + 3
    while time.monotonic() < until:
        with SessionLocal() as observer:
            row = observer.execute(text("""
                SELECT a.pid, a.query_start, a.wait_event_type, l.locktype,
                       pg_blocking_pids(a.pid) AS blocker_pids
                FROM pg_stat_activity AS a
                JOIN pg_locks AS l ON l.pid = a.pid AND l.granted = false
                WHERE a.datname = current_database()
                  AND a.wait_event_type = 'Lock'
                  AND a.query ILIKE '%operation_plan_runs%'
                  AND a.pid <> pg_backend_pid()
                  AND :blocker_pid = ANY(pg_blocking_pids(a.pid))
                ORDER BY a.query_start DESC
                LIMIT 1
            """), {"blocker_pid": blocker_pid}).first()
            if row is not None:
                assert row.query_start < lease_deadline
                assert row.wait_event_type == "Lock" and row.locktype
                assert blocker_pid in row.blocker_pids
                print(f"pg_lock_wait_observed=true blocker_pid_matched=true "
                      f"query_started_before_deadline=true query_start={row.query_start.isoformat()} "
                      f"lease_deadline={lease_deadline.isoformat()}")
                return row.pid
        time.sleep(0.03)
    raise AssertionError(f"no PostgreSQL lock wait observed for run {run_id}")


def _wait_until_lease_expires(deadline: datetime):
    until = time.monotonic() + 6
    while time.monotonic() < until:
        with SessionLocal() as observer:
            db_clock = observer.scalar(select(func.clock_timestamp()))
            if db_clock > deadline:
                print(f"db_clock_before_unlock={db_clock.isoformat()} db_clock_after_deadline=true")
                return
        time.sleep(0.03)
    raise AssertionError("database clock did not pass the lease deadline")


def _deadline(db, run_id: int):
    return db.scalar(select(OperationPlanRun.lease_until).where(OperationPlanRun.id == run_id))


def _run_state(db, run_id: int):
    run = db.get(OperationPlanRun, run_id)
    db.refresh(run)
    return (run.status, run.version, run.claim_token, run.lease_until,
            run.source_run_id, run.actual_count, list(run.source_items or []),
            dict(run.source_metadata or {}), list(run.campaign_ids or []),
            run.blocker_code)


def test_renew_does_not_revive_lease_after_waiting_for_run_lock(client, db):
    user, _first, _config_id, run_id, token = _claimed_source_round(client, db)
    deadline = _deadline(db, run_id)
    baseline = _run_state(db, run_id)
    db.rollback()
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            with SessionLocal() as blocker:
                blocker.scalar(select(OperationPlanRun).where(OperationPlanRun.id == run_id).with_for_update())
                blocker_pid = blocker.scalar(select(func.pg_backend_pid()))
                future = workers.submit(operation_plans.renew_lease, run_id, token)
                _wait_for_db_lock(run_id, deadline, blocker_pid)
                _wait_until_lease_expires(deadline)
                blocker.commit()
            assert future.result(timeout=5) is False
        assert _run_state(db, run_id) == baseline
    finally:
        cleanup(db, user)


def test_renew_accepts_current_claim_but_rejects_wrong_token_or_status(client, db):
    user, _first, _config_id, run_id, token = _claimed_source_round(client, db)
    try:
        assert operation_plans.renew_lease(run_id, token) is True
        lease_after_renew = _deadline(db, run_id)
        db.rollback()
        with SessionLocal() as observer:
            assert lease_after_renew > observer.scalar(select(func.clock_timestamp())) + timedelta(seconds=60)
        assert operation_plans.renew_lease(run_id, "wrong-token") is False
        assert _deadline(db, run_id) == lease_after_renew
        db.execute(text("UPDATE operation_plan_runs SET status = 'interrupted' WHERE id = :run_id"),
                   {"run_id": run_id})
        db.commit()
        assert operation_plans.renew_lease(run_id, token) is False
        assert _deadline(db, run_id) == lease_after_renew
    finally:
        cleanup(db, user)


def test_collected_result_cannot_attach_after_waiting_past_lease(client, db, monkeypatch):
    user, _first, config_id, run_id, token = _claimed_source_round(client, db)
    deadline = _deadline(db, run_id)
    baseline = _run_state(db, run_id)
    db.rollback()
    collection_finished = Event()
    allow_writeback = Event()

    class Ready:
        def ensure_ready(self, _query):
            pass

        def fetch_page(self, _query, _cursor):
            return SourcePage(
                items=[SourceItemIn(platform="fixture", item_kind="product",
                                    external_id="lease-isolated-source", title="隔离来源参考")],
                next_cursor=None, scope_description="隔离来源参考，无热度排名",
            )

    real_collection = collect.execute_collection
    collected_ids = []
    def isolated_collection(session, _config):
        result = real_collection(session, _config)
        result_id = result.id
        collected_ids.append(result_id)
        session.rollback()
        collection_finished.set()
        assert allow_writeback.wait(5)
        return session.get(CollectionRun, result_id)

    monkeypatch.setattr(operation_plans, "get_adapter", lambda _provider: Ready())
    monkeypatch.setattr(collect, "get_adapter", lambda _provider: Ready())
    monkeypatch.setattr(operation_plans, "execute_collection", isolated_collection)
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(operation_plans._collect_source, run_id, token, config_id)
            assert collection_finished.wait(5)
            with SessionLocal() as blocker:
                blocker.scalar(select(OperationPlanRun).where(OperationPlanRun.id == run_id).with_for_update())
                blocker_pid = blocker.scalar(select(func.pg_backend_pid()))
                allow_writeback.set()
                _wait_for_db_lock(run_id, deadline, blocker_pid)
                _wait_until_lease_expires(deadline)
                blocker.commit()
            future.result(timeout=5)
        assert len(collected_ids) == 1
        assert db.get(CollectionRun, collected_ids[0]) is not None
        assert _run_state(db, run_id) == baseline
        assert db.scalar(select(func.count()).select_from(OperationPlanCampaign)
                         .where(OperationPlanCampaign.plan_run_id == run_id)) == 0
    finally:
        allow_writeback.set()
        cleanup(db, user)


def test_preflight_block_cannot_overwrite_expired_claim_after_lock_wait(client, db, monkeypatch):
    user, _first, config_id, run_id, token = _claimed_source_round(client, db)
    deadline = _deadline(db, run_id)
    baseline = _run_state(db, run_id)
    db.rollback()
    entered = Event()
    allow_failure = Event()

    class Denied:
        def ensure_ready(self, _query):
            entered.set()
            assert allow_failure.wait(5)
            raise CredentialsMissing("isolated fixture unavailable")

    monkeypatch.setattr(operation_plans, "get_adapter", lambda _provider: Denied())
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(operation_plans._collect_source, run_id, token, config_id)
            assert entered.wait(5)
            with SessionLocal() as blocker:
                blocker.scalar(select(OperationPlanRun).where(OperationPlanRun.id == run_id).with_for_update())
                blocker_pid = blocker.scalar(select(func.pg_backend_pid()))
                allow_failure.set()
                _wait_for_db_lock(run_id, deadline, blocker_pid)
                _wait_until_lease_expires(deadline)
                blocker.commit()
            future.result(timeout=5)
        assert _run_state(db, run_id) == baseline
    finally:
        allow_failure.set()
        cleanup(db, user)
