"""发布编排。隔离在测试用户上，不调用真实平台。"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select, text

from app.core.db import SessionLocal
from app.integrations.publish.base import PublishAdapter, PublishContext, PublishOutcome
from app.integrations.publish.registry import catalog, get_adapter
from app.models.campaign import Campaign, ContentVariant, VariantReview
from app.models.publish import PublishJob as Job
from app.models.social import SocialAccount
from app.models.source import PlatformConnection
from app.services.publish_jobs import (
    MAYBE_SUBMITTED_NOTICE,
    PublishBlocked,
    claim_due,
    content_blockers,
    reconcile_expired,
    renew_lease,
    run_due,
    schedule_job,
    cancel_job,
    reconfirm_job,
    apply_outcome,
    platform_view,
)
from app.services.variant_review import VersionConflict, edit_variant
from tests.conftest import as_user, cleanup, make_user, suffix
from tests.test_review_versions import _open, _ready_platform, _versions

FROZEN_BODY = "材质是帆布。"
FROZEN_TITLE = "帆布托特"


class _Actor:
    def __init__(self, user) -> None:
        self.id = user.id
        self.role = user.role


class _Fake:
    def __init__(self, kind: str = "uploaded", *, raises: Exception | None = None, query_reliable: bool = False) -> None:
        self.kind = kind
        self.raises = raises
        self.query_reliable = query_reliable
        self.submits = 0
        self.queries = 0
        self.seen = None

    def catalog(self) -> dict:
        return {"server_publish": True, "query_reliable": self.query_reliable, "live": False}

    def check_capability(self, connection) -> list:
        del connection
        return []

    def missing_requirements(self, connection) -> list:
        return self.check_capability(connection)

    def upload(self, ctx):
        return PublishOutcome(kind=self.kind)

    def create_content(self, ctx):
        return PublishOutcome(kind=self.kind)

    def query_status(self, ctx):
        self.queries += 1
        self.seen = ctx
        return PublishOutcome(kind="unknown", error_message="只查询，不重新提交")

    def submit(self, ctx):
        self.submits += 1
        self.seen = ctx
        if self.raises:
            raise self.raises
        return PublishOutcome(kind=self.kind)


@pytest.fixture()
def blobs(monkeypatch):
    state = {"data": b"stored-bytes"}
    monkeypatch.setattr("app.services.storage.object_exists", lambda _key: True)
    monkeypatch.setattr("app.services.storage.get_bytes", lambda _key: state["data"])
    return state


def _future(hours: int = 2) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _due(db, job: Job) -> None:
    job.scheduled_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()


def _live(monkeypatch) -> None:
    monkeypatch.setattr("app.integrations.publish.douyin.live_calls_enabled", lambda: True)
    from app.integrations.publish.credentials import PublishCredential

    monkeypatch.setattr(
        "app.integrations.publish.douyin.resolve_credential",
        lambda connection, **_kwargs: PublishCredential("synthetic-only", connection.external_account_id, "isolated-app"),
    )
    from app.integrations.publish.douyin import DouyinPublishAdapter

    original_catalog = DouyinPublishAdapter.catalog
    monkeypatch.setattr(
        DouyinPublishAdapter,
        "catalog",
        lambda self: {**original_catalog(self), "implemented": True},
    )


def _conn(db, user, *, platform="douyin", purpose="publish", scopes=None, status="connected", credential="vault://publish/test"):
    row = PlatformConnection(
        owner_id=user.id,
        platform=platform,
        purpose=purpose,
        external_account_id=f"acct-{suffix()}",
        status=status,
        scope_set=["video.create.bind"] if scopes is None else scopes,
        credential_ref=credential,
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _approve(client, campaign_id: int, platform: str = "douyin") -> None:
    res = client.post(
        f"/api/campaigns/{campaign_id}/variants/{platform}/approve",
        json={"expected_version": 1, "comment": "通过这一版"},
    )
    assert res.status_code == 200, res.text


def _arm(client, db, *, both: bool = False):
    user, _product, _fact, campaign_id, detail = _open(client, db)
    _ready_platform(db, detail, "douyin")
    _ready_platform(db, detail, "xiaohongshu")
    db.commit()
    _approve(client, campaign_id, "douyin")
    if both:
        _approve(client, campaign_id, "xiaohongshu")
    db.rollback()
    db.expire_all()
    return user, campaign_id


def _post(client, campaign_id: int, *, platform="douyin", connection_id=None, when=None, key=None):
    return client.post(
        f"/api/campaigns/{campaign_id}/publish",
        json={
            "platform": platform,
            "expected_version": 1,
            "scheduled_at": (when or _future()).isoformat(),
            "connection_id": connection_id,
            "idempotency_key": key,
        },
        headers={"Idempotency-Key": key} if key else None,
    )


def _jobs(db, campaign_id: int) -> list[Job]:
    db.rollback()
    db.expire_all()
    return list(db.scalars(select(Job).where(Job.campaign_id == campaign_id).order_by(Job.id.asc())).all())


def _race(calls):
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def run(index, fn):
        session = SessionLocal()
        try:
            barrier.wait(20)
            results[index] = fn(session)
        except Exception as exc:
            session.rollback()
            results[index] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=run, args=(index, fn)) for index, fn in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
        assert not thread.is_alive()
    return results


def _wait_for_pg_lock(pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with SessionLocal() as observer:
            waiting = observer.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            )
        if waiting == "Lock":
            return
        time.sleep(0.02)
    pytest.fail(f"PostgreSQL backend {pid} never entered Lock wait")


def test_catalog_records_verified_sources_and_does_not_pretend(blobs):
    del blobs
    text = json.dumps(catalog(), ensure_ascii=False)
    assert "模拟" not in text
    douyin = get_adapter("douyin").catalog()
    xhs = get_adapter("xiaohongshu").catalog()
    assert douyin["query_reliable"] is False
    assert douyin["scope"] == "video.create.bind"
    assert any("upload-video" in item for item in douyin["sources"])
    assert xhs["server_publish"] is False
    assert xhs["readiness_when_approved"] == "approved_ready_to_publish"
    assert xhs["endpoints"] == []
    outcome = get_adapter("douyin").submit(
        PublishContext(
            platform="douyin",
            copy_snapshot={"title": "x"},
            asset_order=[],
            local_request_id="local",
            connection_id=None,
        )
    )
    assert outcome.provider_request_id is None
    assert outcome.kind != "published"


def test_repeat_schedule_is_one_job_and_snapshot_is_checksummed(client, db, blobs):
    user, campaign_id = _arm(client, db)
    try:
        when = _future()
        first = _post(client, campaign_id, key="same-click", when=when)
        second = _post(client, campaign_id, key="same-click", when=when)
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["id"] == second.json()["id"]
        body = first.json()
        assert body["copy_snapshot"]["title"] == FROZEN_TITLE
        assert body["copy_snapshot"]["body"] == FROZEN_BODY
        assert body["publish_url"] is None
        assert body["provider_request_id"] is None
        digest = hashlib.sha256(blobs["data"]).hexdigest()
        assert [item["sha256"] for item in body["asset_order"]] == [digest, digest]
        assert [item["position"] for item in body["asset_order"]] == [0, 1]
        assert db.scalar(select(func.count()).select_from(Job).where(Job.campaign_id == campaign_id)) == 1
    finally:
        cleanup(db, user)


def test_concurrent_schedule_collapses_to_one_row(client, db, blobs, monkeypatch):
    del blobs
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    conn = _conn(db, user)
    actor = _Actor(user)
    when = _future()
    try:
        def once(session):
            row = schedule_job(
                session,
                actor,
                campaign_id,
                "douyin",
                expected_version=1,
                scheduled_at=when,
                connection_id=conn.id,
                idempotency_key=None,
            )
            return row.id

        results = _race([once, once])
        ids = [item for item in results if isinstance(item, int)]
        assert ids, results
        assert len(set(ids)) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.campaign_id == campaign_id)) == 1
    finally:
        cleanup(db, user)


def test_cross_account_cannot_schedule_or_cancel(client, db, blobs):
    del blobs
    user, campaign_id = _arm(client, db)
    other = make_user(db)
    try:
        own = _conn(db, user)
        foreign = _conn(db, other)
        denied = _post(client, campaign_id, connection_id=foreign.id)
        assert denied.status_code == 404
        created = _post(client, campaign_id, connection_id=own.id)
        assert created.status_code == 200, created.text
        as_user(other)
        assert client.get(f"/api/campaigns/{campaign_id}/publish").status_code == 404
        assert client.post(f"/api/campaigns/{campaign_id}/publish/{created.json()['id']}/cancel").status_code == 404
        as_user(user)
        sample = SocialAccount(
            owner_id=user.id,
            platform="douyin",
            external_account_id=f"sample-{suffix()}",
            display_name="样例联系人",
            status="connected",
            data_source="sample",
        )
        db.add(sample)
        db.commit()
        data_conn = _conn(db, user, purpose="data")
        assert _post(client, campaign_id, connection_id=data_conn.id).status_code == 404
        desk = client.get(f"/api/campaigns/{campaign_id}/publish").json()
        side = next(item for item in desk["platforms"] if item["platform"] == "douyin")
        assert all(row["id"] != sample.id for row in side["connections"])
        assert side["connection_state"] in {"待连接", "已连接"}
    finally:
        cleanup(db, user, other)


def test_missing_permission_is_pending_and_expired_schedule_does_not_auto_send(client, db, blobs, monkeypatch):
    del blobs
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    conn = _conn(db, user, scopes=[], credential=None)
    try:
        created = _post(client, campaign_id, connection_id=conn.id)
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["readiness"] == "pending_connection"
        assert body["status"] == "scheduled"
        assert any("待连接" in item for item in body["missing"])
        desk = client.get(f"/api/campaigns/{campaign_id}/publish").json()
        side = next(item for item in desk["platforms"] if item["platform"] == "douyin")
        assert side["connection_state"] == "待连接"
        job = db.get(Job, body["id"])
        _due(db, job)
        assert run_due(db) is None
        job = _jobs(db, campaign_id)[0]
        assert job.status == "needs_reconfirm"
        assert job.status != "published"
        conn.scope_set = ["video.create.bind"]
        conn.credential_ref = "vault://publish/later"
        db.commit()
        assert run_due(db) is None
        assert _jobs(db, campaign_id)[0].status == "needs_reconfirm"
        again = client.post(
            f"/api/campaigns/{campaign_id}/publish/{job.id}/reconfirm",
            json={"scheduled_at": _future().isoformat()},
        )
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "scheduled"
        assert again.json()["readiness"] == "ready"
        assert again.json()["publish_url"] is None
    finally:
        cleanup(db, user)


def test_upload_and_create_are_not_published_and_timeout_is_unknown(client, db, blobs, monkeypatch):
    del blobs
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    conn = _conn(db, user)
    try:
        uploaded = _Fake("uploaded")
        created = _post(client, campaign_id, connection_id=conn.id)
        job = db.get(Job, created.json()["id"])
        _due(db, job)
        assert run_due(db, adapter_override=uploaded) == job.id
        row = _jobs(db, campaign_id)[0]
        assert row.status == "uploaded"
        assert row.status != "published"
        assert row.provider_request_id is None
        assert row.provider_video_id is None
        assert row.copy_snapshot["title"] == FROZEN_TITLE
        assert uploaded.seen.copy_snapshot["title"] == FROZEN_TITLE
        assert uploaded.submits == 1

        row.status = "cancelled"
        db.commit()
        created_job = _post(client, campaign_id, connection_id=conn.id, key="create-1")
        second = db.get(Job, created_job.json()["id"])
        _due(db, second)
        accepted = _Fake("create_accepted")
        run_due(db, adapter_override=accepted)
        assert _jobs(db, campaign_id)[-1].status == "create_accepted"
        assert _jobs(db, campaign_id)[-1].error_message.find("不是公开发布") >= 0

        _jobs(db, campaign_id)[-1].status = "cancelled"
        db.commit()
        third = db.get(Job, _post(client, campaign_id, connection_id=conn.id, key="timeout-1").json()["id"])
        _due(db, third)
        timed = _Fake(raises=TimeoutError("gateway"))
        run_due(db, adapter_override=timed)
        unknown = _jobs(db, campaign_id)[-1]
        assert unknown.status == "publish_unknown"
        assert "不会自动重新提交" in (unknown.error_message or "")
        assert run_due(db) is None
        assert _jobs(db, campaign_id)[-1].status == "publish_unknown"
        assert _jobs(db, campaign_id)[-1].attempt == 1
    finally:
        cleanup(db, user)


def test_expired_lease_queries_when_reliable_and_never_resubmits(client, db, blobs, monkeypatch):
    del blobs
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    conn = _conn(db, user)
    try:
        job = db.get(Job, _post(client, campaign_id, connection_id=conn.id).json()["id"])
        _due(db, job)
        claimed = claim_due(db)
        assert claimed is not None
        held, token = claimed
        assert renew_lease(db, held.id, token) is True
        assert renew_lease(db, held.id, "wrong-token") is False
        assert claim_due(db) is None
        held = db.get(Job, held.id)
        held.provider_request_id = "upstream-from-test"
        held.lease_until = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        fake = _Fake(query_reliable=True)
        assert reconcile_expired(db, adapter_override=fake) == held.id
        assert fake.queries == 1
        assert fake.submits == 0
        row = _jobs(db, campaign_id)[0]
        assert row.status == "publish_unknown"
        assert row.provider_request_id == "upstream-from-test"
        assert run_due(db) is None
    finally:
        cleanup(db, user)


def test_edit_invalidates_unsubmitted_but_submitting_keeps_snapshot(client, db, blobs, monkeypatch):
    del blobs
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    conn = _conn(db, user)
    try:
        created = _post(client, campaign_id, connection_id=conn.id)
        assert created.status_code == 200, created.text
        edited = client.patch(
            f"/api/campaigns/{campaign_id}/variants/douyin",
            json={"expected_version": 1, "title": FROZEN_TITLE, "body": "材质是帆布。手工缝边。"},
        )
        assert edited.status_code == 200, edited.text
        assert _jobs(db, campaign_id)[0].status == "invalidated"
        assert _jobs(db, campaign_id)[0].copy_snapshot["body"] == FROZEN_BODY
        assert _versions(db, campaign_id, "douyin")[-1].version == 2
        assert claim_due(db) is None
    finally:
        cleanup(db, user)


def test_edit_and_submit_race_keeps_original_snapshot(client, db, blobs, monkeypatch):
    del blobs
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    conn = _conn(db, user)
    actor = _Actor(user)
    try:
        job_id = _post(client, campaign_id, connection_id=conn.id).json()["id"]
        job = db.get(Job, job_id)
        _due(db, job)

        def claim(session):
            return claim_due(session)

        def edit(session):
            return edit_variant(
                session,
                actor,
                campaign_id,
                "douyin",
                expected_version=1,
                title=FROZEN_TITLE,
                body="材质是帆布。手工缝边。",
                hashtags=["箱包"],
            ).version

        results = _race([claim, edit])
        from app.services.variant_review import VersionConflict

        assert all(not isinstance(item, Exception) or isinstance(item, VersionConflict) for item in results), results
        rows = _jobs(db, campaign_id)
        assert len(rows) == 1
        assert rows[0].version == 1
        assert rows[0].copy_snapshot["body"] == FROZEN_BODY
        assert rows[0].copy_snapshot["title"] == FROZEN_TITLE
        assert rows[0].status in {"submitting", "invalidated"}
        assert [item.version for item in _versions(db, campaign_id, "douyin")][-1] >= 2
    finally:
        cleanup(db, user)


def test_campaign_failure_and_xiaohongshu_do_not_block_douyin(client, db, blobs, monkeypatch):
    del blobs
    user, campaign_id = _arm(client, db, both=True)
    _live(monkeypatch)
    conn = _conn(db, user)
    try:
        campaign = db.get(Campaign, campaign_id)
        campaign.status = "failed"
        xhs = db.scalar(
            select(ContentVariant).where(
                ContentVariant.campaign_id == campaign_id,
                ContentVariant.platform == "xiaohongshu",
            )
        )
        xhs.status = "rejected"
        db.commit()
        douyin_variant = db.scalar(
            select(ContentVariant).where(
                ContentVariant.campaign_id == campaign_id,
                ContentVariant.platform == "douyin",
                ContentVariant.version == 1,
            )
        )
        blockers = content_blockers(db, campaign, douyin_variant)
        assert blockers == []
        created = _post(client, campaign_id, connection_id=conn.id)
        assert created.status_code == 200, created.text
        douyin_job = db.get(Job, created.json()["id"])
        douyin_job.scheduled_at = _future(5)
        db.commit()

        xhs.status = "approved"
        db.commit()
        xhs_job = _post(client, campaign_id, platform="xiaohongshu")
        assert xhs_job.status_code == 200, xhs_job.text
        assert xhs_job.json()["readiness"] == "approved_ready_to_publish"
        assert xhs_job.json()["provider_request_id"] is None
        parked = db.get(Job, xhs_job.json()["id"])
        _due(db, parked)
        assert run_due(db) is None
        rows = _jobs(db, campaign_id)
        assert {row.platform: row.status for row in rows}["xiaohongshu"] == "needs_reconfirm"
        assert {row.platform: row.status for row in rows}["douyin"] == "scheduled"

        parked = db.get(Job, xhs_job.json()["id"])
        parked.status = "scheduled"
        parked.readiness = "ready"
        _due(db, parked)
        failed = _Fake("failed")
        run_due(db, adapter_override=failed, allow_unready=True)
        rows = _jobs(db, campaign_id)
        by_platform = {row.platform: row.status for row in rows}
        assert by_platform["xiaohongshu"] == "needs_reconfirm"
        assert failed.submits == 0
        assert by_platform["douyin"] == "scheduled"
        assert content_blockers(db, db.get(Campaign, campaign_id), douyin_variant) == []
    finally:
        cleanup(db, user)


def test_snapshot_drift_and_unreadable_media_do_not_send_latest_copy(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    conn = _conn(db, user)
    try:
        created = _post(client, campaign_id, connection_id=conn.id)
        assert created.status_code == 200, created.text
        variant = db.get(ContentVariant, created.json()["variant_id"])
        variant.title = "临时换成最新标题"
        db.commit()
        job = db.get(Job, created.json()["id"])
        _due(db, job)
        assert claim_due(db) is None
        drifted = _jobs(db, campaign_id)[0]
        assert drifted.status == "failed"
        assert drifted.error_code == "review_mismatch"
        assert drifted.copy_snapshot["title"] == FROZEN_TITLE
        assert "没有向外发送" in (drifted.error_message or "")

        drifted.status = "cancelled"
        variant.title = FROZEN_TITLE
        db.commit()
        again = _post(client, campaign_id, connection_id=conn.id, key="drift-bytes")
        blobs["data"] = b"rewritten-bytes"
        job = db.get(Job, again.json()["id"])
        _due(db, job)
        assert claim_due(db) is None
        changed = [row for row in _jobs(db, campaign_id) if row.id == again.json()["id"]][0]
        assert changed.status == "failed"
        assert changed.error_code == "snapshot_drift"
        assert changed.copy_snapshot["body"] == FROZEN_BODY
    finally:
        cleanup(db, user)


def test_revoke_keeps_inflight_snapshot(client, db, blobs, monkeypatch):
    del blobs
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    conn = _conn(db, user)
    try:
        job = db.get(Job, _post(client, campaign_id, connection_id=conn.id).json()["id"])
        _due(db, job)
        assert claim_due(db) is not None
        revoked = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/revoke",
            json={"expected_version": 1, "comment": "撤回"},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["maybe_submitted"] is True
        assert MAYBE_SUBMITTED_NOTICE in revoked.json()["notice"]
        kept = _jobs(db, campaign_id)[0]
        assert kept.status == "submitting"
        assert kept.copy_snapshot["title"] == FROZEN_TITLE
        edited = client.patch(
            f"/api/campaigns/{campaign_id}/variants/douyin",
            json={"expected_version": 1, "title": FROZEN_TITLE, "body": "材质是帆布。手工缝边。"},
        )
        assert edited.status_code == 200, edited.text
        assert MAYBE_SUBMITTED_NOTICE in (edited.json().get("notice") or "")
        kept = _jobs(db, campaign_id)[0]
        assert kept.status == "submitting"
        assert kept.copy_snapshot["body"] == FROZEN_BODY
        assert kept.version == 1
    finally:
        cleanup(db, user)


def test_empty_media_cannot_be_scheduled(client, db, blobs):
    user, campaign_id = _arm(client, db)
    blobs["data"] = b""
    try:
        denied = _post(client, campaign_id)
        assert denied.status_code == 422
        assert denied.json()["detail"]["code"] == "asset_unreadable"
    finally:
        cleanup(db, user)


def test_cancel_rechecks_state_after_a_competing_claim(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    job = db.get(Job, _post(client, campaign_id, connection_id=connection.id).json()["id"])
    _due(db, job)
    entered = threading.Event()
    proceed = threading.Event()
    from app.services import publish_jobs

    original_lock = publish_jobs._lock_campaign

    def pause_after_preview(session, actor, selected_campaign):
        entered.set()
        assert proceed.wait(10)
        return original_lock(session, actor, selected_campaign)

    monkeypatch.setattr(publish_jobs, "_lock_campaign", pause_after_preview)
    result = []

    def cancel():
        with SessionLocal() as session:
            try:
                result.append(cancel_job(session, _Actor(user), job.id).status)
            except PublishBlocked as exc:
                result.append(exc.code)

    thread = threading.Thread(target=cancel)
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as other:
            assert claim_due(other) is not None
        proceed.set()
        thread.join(15)
        assert not thread.is_alive()
        assert result == ["maybe_submitted"]
        assert _jobs(db, campaign_id)[0].status == "submitting"
    finally:
        proceed.set()
        thread.join(15)
        cleanup(db, user)


def test_reconfirm_rechecks_invalidated_job_and_current_review(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    job = db.get(Job, _post(client, campaign_id).json()["id"])
    job.status = "needs_reconfirm"
    db.commit()
    entered = threading.Event()
    proceed = threading.Event()
    from app.services import publish_jobs

    original_lock = publish_jobs._lock_campaign

    def pause_after_preview(session, actor, selected_campaign):
        entered.set()
        assert proceed.wait(10)
        return original_lock(session, actor, selected_campaign)

    monkeypatch.setattr(publish_jobs, "_lock_campaign", pause_after_preview)
    result = []

    def reconfirm():
        with SessionLocal() as session:
            try:
                result.append(reconfirm_job(session, _Actor(user), job.id, scheduled_at=_future()).status)
            except PublishBlocked as exc:
                result.append(exc.code)

    thread = threading.Thread(target=reconfirm)
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as other:
            edit_variant(
                other, _Actor(user), campaign_id, "douyin", expected_version=1,
                title=FROZEN_TITLE, body="材质是帆布。手工缝边。", hashtags=["箱包"],
            )
        proceed.set()
        thread.join(15)
        assert not thread.is_alive()
        assert result == ["reconfirm_closed"]
        assert _jobs(db, campaign_id)[0].status == "invalidated"
    finally:
        proceed.set()
        thread.join(15)
        cleanup(db, user)


def test_reconfirm_reloads_previously_read_approval_state(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    job = db.get(Job, _post(client, campaign_id).json()["id"])
    job.status = "needs_reconfirm"
    db.commit()
    entered = threading.Event()
    proceed = threading.Event()
    from app.services import publish_jobs

    original_lock = publish_jobs._lock_campaign

    def pause(session, actor, selected_campaign):
        if threading.current_thread().name == "preloaded-reconfirm":
            entered.set()
            assert proceed.wait(10)
        return original_lock(session, actor, selected_campaign)

    monkeypatch.setattr(publish_jobs, "_lock_campaign", pause)
    results = []

    def reconfirm():
        with SessionLocal() as session:
            preloaded_variant = session.get(ContentVariant, job.variant_id)
            preloaded_review = session.get(VariantReview, job.review_id)
            assert preloaded_variant.status == "approved"
            assert preloaded_review.decision == "approved"
            try:
                results.append(reconfirm_job(session, _Actor(user), job.id, scheduled_at=_future()).status)
            except PublishBlocked as exc:
                results.append(exc.code)

    thread = threading.Thread(target=reconfirm, name="preloaded-reconfirm")
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as other:
            other.scalar(select(Campaign.id).where(Campaign.id == campaign_id).with_for_update())
            variant = other.get(ContentVariant, job.variant_id)
            variant.status = "needs_review"
            other.commit()
        proceed.set()
        thread.join(10)
        assert not thread.is_alive()
        assert results == ["not_approved"]
        assert _jobs(db, campaign_id)[0].status == "needs_reconfirm"
    finally:
        proceed.set()
        thread.join(10)
        cleanup(db, user)


def test_expired_token_cannot_apply_outcome_without_takeover(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    job = db.get(Job, _post(client, campaign_id, connection_id=connection.id).json()["id"])
    _due(db, job)
    try:
        claimed, token = claim_due(db)
        db.execute(text("UPDATE publish_jobs SET lease_until = clock_timestamp() - interval '1 second' WHERE id = :id"), {"id": job.id})
        db.commit()
        outcome = PublishOutcome(kind="create_accepted", provider_request_id="late-request", provider_video_id="late-video")
        assert apply_outcome(db, claimed.id, token, outcome) is None
        row = _jobs(db, campaign_id)[0]
        assert row.status == "submitting"
        assert row.provider_request_id is None
        assert row.provider_video_id is None
    finally:
        cleanup(db, user)


def test_apply_outcome_rechecks_database_time_after_waiting_for_job_lock(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    job = db.get(Job, _post(client, campaign_id, connection_id=connection.id).json()["id"])
    _due(db, job)
    claimed, token = claim_due(db)
    db.execute(text("UPDATE publish_jobs SET lease_until = clock_timestamp() + interval '2 seconds' WHERE id = :id"), {"id": job.id})
    db.commit()
    lock_held = threading.Event()
    release_lock = threading.Event()
    applying = threading.Event()
    result = []
    worker_pid = []

    def hold_lock():
        with SessionLocal() as session:
            session.scalar(select(Job.id).where(Job.id == job.id).with_for_update())
            lock_held.set()
            assert release_lock.wait(10)
            session.commit()

    def apply():
        with SessionLocal() as session:
            worker_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
            applying.set()
            try:
                result.append(apply_outcome(session, claimed.id, token, PublishOutcome(
                    kind="create_accepted", provider_request_id="late-after-lock",
                )))
            except Exception as exc:
                result.append(exc)

    holder = threading.Thread(target=hold_lock)
    worker = threading.Thread(target=apply)
    try:
        holder.start()
        assert lock_held.wait(10)
        worker.start()
        assert applying.wait(10)
        _wait_for_pg_lock(worker_pid[0])
        time.sleep(2.1)
        release_lock.set()
        holder.join(10)
        worker.join(10)
        assert not holder.is_alive() and not worker.is_alive()
        assert result == [None], result
        row = _jobs(db, campaign_id)[0]
        assert row.status == "submitting"
        assert row.provider_request_id is None
    finally:
        release_lock.set()
        holder.join(10)
        worker.join(10)
        cleanup(db, user)


def test_renew_lease_cannot_revive_token_after_waiting_for_job_lock(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    job = db.get(Job, _post(client, campaign_id, connection_id=connection.id).json()["id"])
    _due(db, job)
    claimed, token = claim_due(db)
    db.execute(text("UPDATE publish_jobs SET lease_until = clock_timestamp() + interval '2 seconds' WHERE id = :id"), {"id": job.id})
    db.commit()
    lock_held = threading.Event()
    release_lock = threading.Event()
    renewing = threading.Event()
    result = []
    worker_pid = []

    def hold_lock():
        with SessionLocal() as session:
            session.scalar(select(Job.id).where(Job.id == job.id).with_for_update())
            lock_held.set()
            assert release_lock.wait(10)
            session.commit()

    def renew():
        with SessionLocal() as session:
            worker_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
            renewing.set()
            try:
                result.append(renew_lease(session, claimed.id, token))
            except Exception as exc:
                result.append(exc)

    holder = threading.Thread(target=hold_lock)
    worker = threading.Thread(target=renew)
    try:
        holder.start()
        assert lock_held.wait(10)
        worker.start()
        assert renewing.wait(10)
        _wait_for_pg_lock(worker_pid[0])
        time.sleep(2.1)
        release_lock.set()
        holder.join(10)
        worker.join(10)
        assert not holder.is_alive() and not worker.is_alive()
        assert result == [False], result
        with SessionLocal() as observer:
            assert observer.scalar(select(Job.lease_until > func.clock_timestamp()).where(Job.id == job.id)) is False
    finally:
        release_lock.set()
        holder.join(10)
        worker.join(10)
        cleanup(db, user)


def test_unreviewed_content_and_selected_account_capability(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db, both=True)
    _live(monkeypatch)
    connection = _conn(db, user)
    try:
        campaign = db.get(Campaign, campaign_id)
        douyin = platform_view(db, _Actor(user), campaign, "douyin")
        xhs = platform_view(db, _Actor(user), campaign, "xiaohongshu")
        assert douyin["content_ready"] is True
        assert douyin["readiness"] != "ready"
        assert xhs["readiness"] == "approved_ready_to_publish"
        assert get_adapter("douyin").catalog()["implemented"] is True
        connection_views = {row["id"]: row for row in douyin["connections"]}
        assert connection_views[connection.id]["readiness"] == "ready"
        weak_connection = _conn(db, user, scopes=[], credential=None)
        switched = platform_view(db, _Actor(user), campaign, "douyin")
        by_id = {row["id"]: row for row in switched["connections"]}
        assert by_id[connection.id]["missing"] != by_id[weak_connection.id]["missing"]

        xhs_variant = db.get(ContentVariant, xhs["variant_id"])
        xhs_variant.status = "blocked"
        db.execute(delete(VariantReview).where(VariantReview.variant_id == xhs_variant.id))
        db.commit()
        blocked = platform_view(db, _Actor(user), campaign, "xiaohongshu")
        assert blocked["content_ready"] is False
        assert blocked["readiness"] != "approved_ready_to_publish"
        xhs_variant.status = "needs_review"
        db.commit()
        unreviewed = platform_view(db, _Actor(user), campaign, "xiaohongshu")
        assert unreviewed["readiness"] != "approved_ready_to_publish"
        assert unreviewed["review_id"] is None

        scheduled = _post(client, campaign_id, connection_id=connection.id)
        assert scheduled.status_code == 200, scheduled.text
        assert scheduled.json()["readiness"] == "ready"
    finally:
        cleanup(db, user)


def test_idempotency_key_rejects_changed_schedule_time(client, db, blobs):
    user, campaign_id = _arm(client, db)
    try:
        first = _post(client, campaign_id, key="same-key", when=_future(2))
        assert first.status_code == 200, first.text
        changed = _post(client, campaign_id, key="same-key", when=_future(3))
        assert changed.status_code == 409, changed.text
        assert changed.json()["detail"]["code"] == "idempotency_conflict"
        assert len(_jobs(db, campaign_id)) == 1
    finally:
        cleanup(db, user)


def test_idempotency_key_reused_for_another_campaign_is_http_409(client, db, blobs):
    first_user, first_campaign = _arm(client, db)
    second_user, second_campaign = _arm(client, db)
    db.get(Campaign, second_campaign).owner_id = first_user.id
    db.commit()
    as_user(first_user)
    try:
        first = _post(client, first_campaign, key="another-campaign")
        assert first.status_code == 200, first.text
        conflict = _post(client, second_campaign, key="another-campaign")
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    finally:
        cleanup(db, first_user, second_user)


def test_idempotency_unique_race_across_campaigns_conflicts(client, db, blobs, monkeypatch):
    first_user, first_campaign = _arm(client, db)
    second_user, second_campaign = _arm(client, db)
    db.get(Campaign, second_campaign).owner_id = first_user.id
    db.commit()
    from app.services import publish_jobs

    commit = publish_jobs._commit_locked
    waiting = threading.Barrier(2)

    def synchronized_commit(session):
        if any(isinstance(item, Job) for item in session.new):
            waiting.wait(10)
        return commit(session)

    monkeypatch.setattr(publish_jobs, "_commit_locked", synchronized_commit)
    actor = _Actor(first_user)
    when = _future()

    def schedule(campaign_id):
        def once(session):
            try:
                return schedule_job(
                    session, actor, campaign_id, "douyin", expected_version=1,
                    scheduled_at=when, connection_id=None, idempotency_key="cross-campaign-key",
                ).id
            except PublishBlocked as exc:
                return exc.code

        return once

    try:
        results = _race([schedule(first_campaign), schedule(second_campaign)])
        assert len([item for item in results if isinstance(item, int)]) == 1, results
        assert results.count("idempotency_conflict") == 1, results
    finally:
        cleanup(db, first_user, second_user)


def test_run_due_renews_lease_during_slow_submit(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    job = db.get(Job, _post(client, campaign_id, connection_id=connection.id).json()["id"])
    _due(db, job)
    from app.services import publish_jobs

    monkeypatch.setattr(publish_jobs, "LEASE", timedelta(seconds=1))
    entered = threading.Event()
    release = threading.Event()
    results = []

    class Slow(_Fake):
        session = None

        def submit(self, ctx):
            assert self.session is not None
            assert not self.session.in_transaction()
            entered.set()
            assert release.wait(10)
            return PublishOutcome(kind="uploaded")

    def execute():
        with SessionLocal() as session:
            adapter = Slow()
            adapter.session = session
            results.append(run_due(session, adapter_override=adapter))

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(10)
        time.sleep(1.35)
        with SessionLocal() as observer:
            alive = observer.scalar(
                select(Job.lease_until > func.clock_timestamp()).where(Job.id == job.id)
            )
        assert alive is True
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert results == [job.id]
        assert _jobs(db, campaign_id)[0].status == "uploaded"
    finally:
        release.set()
        thread.join(10)
        cleanup(db, user)


def test_run_due_stops_before_create_after_losing_claim(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    job = db.get(Job, _post(client, campaign_id, connection_id=connection.id).json()["id"])
    _due(db, job)
    entered = threading.Event()
    release = threading.Event()
    results = []

    class TwoStage(PublishAdapter):
        platform = "douyin"
        creates = 0

        def catalog(self):
            return {"server_publish": True, "implemented": True, "query_reliable": False}

        def check_capability(self, connection):
            return []

        def upload(self, ctx):
            entered.set()
            assert release.wait(10)
            deadline = time.monotonic() + 1
            while ctx.ensure_claim() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not ctx.ensure_claim(), "claim loss was not observed before upload finished"
            return PublishOutcome(kind="uploaded", provider_request_id="late-upload")

        def create_content(self, ctx):
            self.creates += 1
            return PublishOutcome(kind="create_accepted", provider_video_id="should-not-exist")

        def query_status(self, ctx):
            return PublishOutcome(kind="unknown")

    adapter = TwoStage()

    def execute():
        with SessionLocal() as session:
            try:
                results.append(run_due(session, adapter_override=adapter))
            except Exception as exc:
                results.append(exc)

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as other:
            other.execute(text("UPDATE publish_jobs SET claim_token = 'taken-over' WHERE id = :id"), {"id": job.id})
            other.commit()
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert results == [job.id]
        assert adapter.creates == 0
        row = _jobs(db, campaign_id)[0]
        assert row.status == "submitting"
        assert row.provider_request_id is None
        assert row.provider_video_id is None
    finally:
        release.set()
        thread.join(10)
        cleanup(db, user)


def test_run_due_stops_when_lease_renewal_fails_without_token_change(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    job = db.get(Job, _post(client, campaign_id, connection_id=connection.id).json()["id"])
    _due(db, job)
    from app.services import publish_jobs

    monkeypatch.setattr(publish_jobs, "LEASE", timedelta(seconds=5))
    renew_failed = threading.Event()

    def fail_renew(session, job_id, token, *, commit=True):
        with SessionLocal() as independent:
            assert independent is not session
        renew_failed.set()
        return False

    monkeypatch.setattr(publish_jobs, "renew_lease", fail_renew)
    entered = threading.Event()
    release = threading.Event()

    class TwoStage(PublishAdapter):
        platform = "douyin"
        creates = 0

        def catalog(self):
            return {"server_publish": True, "implemented": True, "query_reliable": False}

        def check_capability(self, connection):
            return []

        def upload(self, ctx):
            entered.set()
            assert release.wait(10)
            deadline = time.monotonic() + 1
            while ctx.ensure_claim() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not ctx.ensure_claim(), "heartbeat failure was not observed before upload finished"
            return PublishOutcome(kind="uploaded", provider_request_id="late-upload")

        def create_content(self, ctx):
            self.creates += 1
            return PublishOutcome(kind="create_accepted", provider_video_id="late-create")

        def query_status(self, ctx):
            return PublishOutcome(kind="unknown")

    adapter = TwoStage()
    results = []

    def execute():
        with SessionLocal() as session:
            try:
                results.append(run_due(session, adapter_override=adapter))
            except Exception as exc:
                results.append(exc)

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(10)
        assert renew_failed.wait(4)
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert results == [job.id]
        assert adapter.creates == 0
        row = _jobs(db, campaign_id)[0]
        assert row.status == "submitting"
        assert row.provider_request_id is None
        assert row.provider_video_id is None
    finally:
        release.set()
        thread.join(10)
        cleanup(db, user)


def test_media_io_for_schedule_and_claim_runs_outside_transaction(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    actor = _Actor(user)
    try:
        with SessionLocal() as session:
            calls = []

            def exists(_key):
                calls.append(("head", session.in_transaction()))
                assert not session.in_transaction()
                return True

            def read(_key):
                calls.append(("get", session.in_transaction()))
                assert not session.in_transaction()
                return blobs["data"]

            monkeypatch.setattr("app.services.storage.object_exists", exists)
            monkeypatch.setattr("app.services.storage.get_bytes", read)
            job = schedule_job(
                session, actor, campaign_id, "douyin", expected_version=1,
                scheduled_at=_future(), connection_id=connection.id, idempotency_key=None,
            )
            job.scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
            assert {kind for kind, _ in calls} == {"head", "get"}
            calls.clear()
            assert claim_due(session) is not None
            assert {kind for kind, _ in calls} == {"head", "get"}
            assert all(not in_transaction for _, in_transaction in calls)
    finally:
        cleanup(db, user)


def test_schedule_rechecks_review_after_media_io_allows_concurrent_edit(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    actor = _Actor(user)
    in_storage = threading.Event()
    release_storage = threading.Event()
    edit_done = threading.Event()
    results = []

    def read(_key):
        if not in_storage.is_set():
            in_storage.set()
            assert release_storage.wait(10)
        return blobs["data"]

    monkeypatch.setattr("app.services.storage.get_bytes", read)

    def schedule():
        with SessionLocal() as session:
            try:
                results.append(schedule_job(
                    session, actor, campaign_id, "douyin", expected_version=1,
                    scheduled_at=_future(), connection_id=None, idempotency_key=None,
                ).id)
            except Exception as exc:
                results.append(exc)

    def edit():
        with SessionLocal() as session:
            try:
                edit_variant(
                    session, actor, campaign_id, "douyin", expected_version=1,
                    title=FROZEN_TITLE, body="材质是帆布。手工缝边。", hashtags=["箱包"],
                )
            finally:
                edit_done.set()

    schedule_thread = threading.Thread(target=schedule)
    edit_thread = threading.Thread(target=edit)
    try:
        schedule_thread.start()
        assert in_storage.wait(10)
        edit_thread.start()
        assert edit_done.wait(3), "media I/O still holds the campaign lock"
        release_storage.set()
        schedule_thread.join(10)
        edit_thread.join(10)
        assert not schedule_thread.is_alive()
        assert len(results) == 1
        assert isinstance(results[0], (PublishBlocked, VersionConflict)), results
        assert len(_jobs(db, campaign_id)) == 0
        assert _versions(db, campaign_id, "douyin")[-1].version == 2
    finally:
        release_storage.set()
        schedule_thread.join(10)
        edit_thread.join(10)
        cleanup(db, user)


def test_claim_rechecks_review_after_media_io_allows_concurrent_edit(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    job = db.get(Job, _post(client, campaign_id, connection_id=connection.id).json()["id"])
    _due(db, job)
    actor = _Actor(user)
    in_storage = threading.Event()
    release_storage = threading.Event()
    edit_done = threading.Event()
    results = []

    def read(_key):
        if not in_storage.is_set():
            in_storage.set()
            assert release_storage.wait(10)
        return blobs["data"]

    monkeypatch.setattr("app.services.storage.get_bytes", read)

    def claim():
        with SessionLocal() as session:
            try:
                results.append(claim_due(session))
            except Exception as exc:
                results.append(exc)

    def edit():
        with SessionLocal() as session:
            try:
                edit_variant(
                    session, actor, campaign_id, "douyin", expected_version=1,
                    title=FROZEN_TITLE, body="材质是帆布。手工缝边。", hashtags=["箱包"],
                )
            finally:
                edit_done.set()

    claim_thread = threading.Thread(target=claim)
    edit_thread = threading.Thread(target=edit)
    try:
        claim_thread.start()
        assert in_storage.wait(10)
        edit_thread.start()
        assert edit_done.wait(3), "claim media I/O still holds the campaign lock"
        release_storage.set()
        claim_thread.join(10)
        edit_thread.join(10)
        assert not claim_thread.is_alive() and not edit_thread.is_alive()
        assert results == [None], results
        assert _jobs(db, campaign_id)[0].status == "invalidated"
    finally:
        release_storage.set()
        claim_thread.join(10)
        edit_thread.join(10)
        cleanup(db, user)


def test_reconcile_loses_claim_during_query_without_late_write(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    job = db.get(Job, _post(client, campaign_id, connection_id=connection.id).json()["id"])
    _due(db, job)
    claimed, _token = claim_due(db)
    claimed.provider_request_id = "existing-upstream"
    claimed.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    entered = threading.Event()
    release = threading.Event()
    results = []

    class SlowQuery(_Fake):
        session = None

        def __init__(self):
            super().__init__(query_reliable=True)

        def query_status(self, ctx):
            assert self.session is not None
            assert not self.session.in_transaction()
            entered.set()
            assert release.wait(10)
            return PublishOutcome(kind="create_accepted", provider_video_id="late-video")

    def reconcile():
        with SessionLocal() as session:
            adapter = SlowQuery()
            adapter.session = session
            try:
                results.append(reconcile_expired(session, adapter_override=adapter))
            except Exception as exc:
                results.append(exc)

    thread = threading.Thread(target=reconcile)
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as other:
            other.execute(text("UPDATE publish_jobs SET claim_token = 'new-owner' WHERE id = :id"), {"id": job.id})
            other.commit()
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert results == [job.id]
        row = _jobs(db, campaign_id)[0]
        assert row.status == "submitting"
        assert row.provider_request_id == "existing-upstream"
        assert row.provider_video_id is None
    finally:
        release.set()
        thread.join(10)
        cleanup(db, user)


def test_run_due_passes_exact_verified_media_bytes_to_adapter(client, db, blobs, monkeypatch):
    user, campaign_id = _arm(client, db)
    _live(monkeypatch)
    connection = _conn(db, user)
    job = db.get(Job, _post(client, campaign_id, connection_id=connection.id).json()["id"])
    _due(db, job)

    class Inspect(_Fake):
        def submit(self, ctx):
            assert ctx.asset_bytes == (b"stored-bytes", b"stored-bytes")
            blobs["data"] = b"changed-after-validation"
            return PublishOutcome(kind="uploaded")

    try:
        assert run_due(db, adapter_override=Inspect()) == job.id
        assert _jobs(db, campaign_id)[0].status == "uploaded"
    finally:
        cleanup(db, user)
