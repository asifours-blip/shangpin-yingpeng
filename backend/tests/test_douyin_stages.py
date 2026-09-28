"""真实 HTTP 组装进入发布服务，节点各自先落意图再出网。"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.integrations.publish.douyin import DouyinPublishAdapter
from app.models.publish import PublishJob
from app.services.publish_jobs import claim_due, reconcile_expired, run_due, _stage_intent
from app.services.publish_oauth import disconnect
from tests.conftest import cleanup
from tests.test_publish_jobs import _arm, _conn, _due, _post


VIDEO = (Path(__file__).parent / "fixtures" / "publish-one-second.mp4").read_bytes()
COVER = (Path(__file__).parent / "fixtures" / "canvas-tote.jpg").read_bytes()


def _secret(monkeypatch, connection):
    monkeypatch.setenv("DOUYIN_CLIENT_KEY", "isolated-app")
    monkeypatch.setenv("CODEX_DOUYIN_STAGE_SECRET", json.dumps({
        "owner_id": connection.owner_id, "connection_id": connection.id, "platform": "douyin",
        "purpose": "publish", "open_id": connection.external_account_id,
        "scopes": ["video.create.bind"],
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "client_key": "isolated-app", "access_token": "synthetic-stage-token",
    }))
    connection.credential_ref = "env:CODEX_DOUYIN_STAGE_SECRET"


def _media(monkeypatch):
    monkeypatch.setattr("app.services.storage.object_exists", lambda _key: True)
    monkeypatch.setattr("app.services.storage.get_bytes", lambda key: COVER if "cover" in key else VIDEO)


def _adapter(requests):
    def handle(request):
        requests.append(request)
        if request.url.path.endswith("upload_image/"):
            return httpx.Response(200, json={"data": {"error_code": 0, "image": {"image_id": "cover-1"}}, "extra": {"error_code": 0}})
        if request.url.path.endswith("upload_video/"):
            return httpx.Response(200, json={"data": {"error_code": 0, "video": {"video_id": "video-1"}}, "extra": {"error_code": 0}})
        return httpx.Response(200, json={"data": {"error_code": 0, "item_id": "item-1"}, "extra": {"error_code": 0}})
    return DouyinPublishAdapter(transport=httpx.MockTransport(handle))


def _scheduled(client, db, monkeypatch):
    _media(monkeypatch)
    user, campaign_id = _arm(client, db)
    connection = _conn(db, user)
    _secret(monkeypatch, connection)
    db.commit()
    response = _post(client, campaign_id, connection_id=connection.id)
    assert response.status_code == 200, response.text
    job = db.get(PublishJob, response.json()["id"])
    _due(db, job)
    return user, campaign_id, connection, job


def test_real_http_stages_persist_ids_and_create_is_not_published(client, db, monkeypatch):
    _media(monkeypatch)
    user, campaign_id = _arm(client, db)
    connection = _conn(db, user)
    _secret(monkeypatch, connection)
    db.commit()
    requests = []

    def handle(request):
        assert db.in_transaction() is False
        requests.append(request)
        if request.url.path.endswith("upload_image/"):
            assert COVER in request.content
            return httpx.Response(200, json={"data": {"error_code": 0, "image": {"image_id": "cover-1"}}, "extra": {"error_code": 0, "logid": "cover-log"}})
        if request.url.path.endswith("upload_video/"):
            assert VIDEO in request.content
            return httpx.Response(200, json={"data": {"error_code": 0, "video": {"video_id": "video-upload-1"}}, "extra": {"error_code": 0, "logid": "video-log"}})
        assert request.url.path.endswith("create_video/")
        assert json.loads(request.content)["custom_cover_image_url"] == "cover-1"
        assert json.loads(request.content)["video_id"] == "video-upload-1"
        return httpx.Response(200, json={"data": {"error_code": 0, "item_id": "content-item-1", "video_id": "content-video-1"}, "extra": {"error_code": 0, "logid": "create-log"}})

    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(handle))
    try:
        response = _post(client, campaign_id, connection_id=connection.id)
        assert response.status_code == 200, response.text
        job = db.get(PublishJob, response.json()["id"])
        _due(db, job)
        for expected_phase in ("cover_uploaded", "video_uploaded", "done"):
            assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
            db.rollback()
            db.refresh(job)
            assert job.phase == expected_phase
        assert [request.url.path.rsplit("/", 2)[-2] for request in requests] == ["upload_image", "upload_video", "create_video"]
        assert job.status == "create_accepted"
        assert (job.cover_image_id, job.video_upload_id) == ("cover-1", "video-upload-1")
        assert (job.content_item_id, job.content_video_id) == ("content-item-1", "content-video-1")
        assert (job.cover_log_id, job.video_log_id, job.create_log_id) == ("cover-log", "video-log", "create-log")
        assert job.provider_request_id is None and job.provider_video_id is None
    finally:
        cleanup(db, user)


def test_confirmed_uploads_continue_after_edit_using_original_snapshot(client, db, monkeypatch):
    user, campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    requests = []
    adapter = _adapter(requests)
    try:
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        edited = client.patch(
            f"/api/campaigns/{campaign_id}/variants/douyin",
            json={"expected_version": 1, "title": "新版本标题", "body": "这是新版本，不该发送"},
        )
        assert edited.status_code == 200, edited.text
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        db.rollback(); db.refresh(job)
        assert job.status == "create_accepted"
        assert json.loads(requests[-1].content)["text"] == "帆布托特\n材质是帆布。\n#箱包"
    finally:
        cleanup(db, user)


def test_confirmed_checkpoint_survives_expiry_before_next_intent(client, db, monkeypatch):
    user, _campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    requests = []
    adapter = _adapter(requests)
    try:
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        db.rollback(); db.refresh(job)
        assert job.phase == "cover_uploaded"
        claimed, token = claim_due(db, allow_unready=True)
        assert claimed.id == job.id and claimed.phase == "cover_uploaded"
        claimed.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert reconcile_expired(db, adapter_override=adapter) == job.id
        db.rollback(); db.refresh(job)
        assert job.status == "submitting" and job.claim_token is None and job.phase == "cover_uploaded"
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        assert [request.url.path.rsplit("/", 2)[-2] for request in requests] == ["upload_image", "upload_video"]
    finally:
        cleanup(db, user)


def test_create_intent_expiry_never_recreates(client, db, monkeypatch):
    user, _campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    requests = []
    adapter = _adapter(requests)
    try:
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        db.rollback(); db.refresh(job)
        claimed, token = claim_due(db, allow_unready=True)
        assert claimed.phase == "video_uploaded"
        db.rollback()
        assert _stage_intent(db, job.id, token, "video_uploaded", "create")
        db.rollback(); db.refresh(job)
        job.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert reconcile_expired(db, adapter_override=adapter) == job.id
        db.rollback(); db.refresh(job)
        assert job.status == "publish_unknown" and job.error_code == "create_unknown"
        assert run_due(db, adapter_override=adapter, allow_unready=True) is None
        assert len(requests) == 2
    finally:
        cleanup(db, user)


def test_connection_change_after_cover_prevents_next_http_without_cancellable_regression(client, db, monkeypatch):
    user, _campaign_id, connection, job = _scheduled(client, db, monkeypatch)
    requests = []
    adapter = _adapter(requests)
    try:
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        connection.external_account_id = "another-open-id"
        db.commit()
        assert run_due(db, adapter_override=adapter, allow_unready=True) is None
        db.rollback(); db.refresh(job)
        assert job.status == "publish_unknown" and job.phase == "cover_uploaded"
        assert len(requests) == 1
    finally:
        cleanup(db, user)


def test_missing_secret_after_cover_keeps_checkpoint_and_can_resume(client, db, monkeypatch):
    user, _campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    requests = []
    adapter = _adapter(requests)
    try:
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        monkeypatch.delenv("CODEX_DOUYIN_STAGE_SECRET")
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        db.rollback(); db.refresh(job)
        assert job.status == "submitting" and job.phase == "cover_uploaded"
        assert job.claim_token is None and len(requests) == 1
        _secret(monkeypatch, _connection)
        job.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        assert [request.url.path.rsplit("/", 2)[-2] for request in requests] == ["upload_image", "upload_video"]
    finally:
        cleanup(db, user)


def test_blocked_checkpoint_does_not_starve_a_second_ready_job(client, db, monkeypatch):
    first_user, _first_campaign, _first_connection, first = _scheduled(client, db, monkeypatch)
    requests = []
    adapter = _adapter(requests)
    second_user = None
    try:
        assert run_due(db, adapter_override=adapter, allow_unready=True) == first.id
        monkeypatch.delenv("CODEX_DOUYIN_STAGE_SECRET")
        assert run_due(db, adapter_override=adapter, allow_unready=True) == first.id
        db.rollback(); db.refresh(first)
        assert first.status == "submitting" and first.next_attempt_at > datetime.now(timezone.utc)
        second_user, _campaign, _connection, second = _scheduled(client, db, monkeypatch)
        assert run_due(db, adapter_override=adapter, allow_unready=True) == second.id
        db.rollback(); db.refresh(first); db.refresh(second)
        assert first.phase == "cover_uploaded" and second.phase == "cover_uploaded"
        assert [request.url.path.rsplit("/", 2)[-2] for request in requests] == ["upload_image", "upload_image"]
    finally:
        if second_user is not None:
            cleanup(db, second_user)
        cleanup(db, first_user)


def test_live_closed_cannot_send_even_with_allow_unready(client, db, monkeypatch):
    user, _campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)
    try:
        assert run_due(db, allow_unready=True) is None
        db.rollback(); db.refresh(job)
        assert job.status == "scheduled"
    finally:
        cleanup(db, user)


def test_live_on_without_bound_secret_is_pending_in_publish_api(client, db, monkeypatch):
    user, campaign_id, connection, _job = _scheduled(client, db, monkeypatch)
    monkeypatch.setenv("PUBLISH_LIVE", "1")
    monkeypatch.delenv("CODEX_DOUYIN_STAGE_SECRET")
    try:
        response = client.get(f"/api/campaigns/{campaign_id}/publish")
        assert response.status_code == 200, response.text
        douyin = next(side for side in response.json()["platforms"] if side["platform"] == "douyin")
        selected = next(item for item in douyin["connections"] if item["id"] == connection.id)
        assert selected["readiness"] == "pending_connection"
        assert "部署凭证" in "".join(selected["missing"])
    finally:
        cleanup(db, user)


def test_second_claim_cannot_replace_active_token_after_stale_preview(client, db, monkeypatch):
    user, _campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    from app.services import publish_jobs

    original_read = publish_jobs._read_media
    previewed = threading.Event()
    let_second_continue = threading.Event()
    http_entered = threading.Event()
    finish_http = threading.Event()
    requests = []
    results = {}

    def paused_media(entries):
        data = original_read(entries)
        if threading.current_thread().name == "second-claim":
            previewed.set()
            assert let_second_continue.wait(10)
        return data

    def handle(request):
        requests.append(request)
        http_entered.set()
        assert finish_http.wait(10)
        return httpx.Response(200, json={"data": {"error_code": 0, "image": {"image_id": "cover-1"}}, "extra": {"error_code": 0}})

    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(publish_jobs, "_read_media", paused_media)

    def second():
        try:
            with SessionLocal() as session:
                results["second"] = claim_due(session, allow_unready=True)
        except Exception as exc:
            results["second_error"] = exc

    def first():
        try:
            with SessionLocal() as session:
                results["first"] = run_due(session, adapter_override=adapter, allow_unready=True)
        except Exception as exc:
            results["first_error"] = exc

    second_thread = threading.Thread(target=second, name="second-claim")
    first_thread = threading.Thread(target=first, name="first-claim")
    try:
        second_thread.start()
        assert previewed.wait(10)
        first_thread.start()
        assert http_entered.wait(10)
        let_second_continue.set()
        second_thread.join(10)
        assert not second_thread.is_alive() and "second_error" not in results
        assert results["second"] is None
        finish_http.set()
        first_thread.join(10)
        assert not first_thread.is_alive() and "first_error" not in results
        assert results["first"] == job.id and len(requests) == 1
    finally:
        let_second_continue.set(); finish_http.set()
        second_thread.join(10); first_thread.join(10)
        cleanup(db, user)


def test_slow_real_http_upload_renews_lease(client, db, monkeypatch):
    user, _campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    from app.services import publish_jobs
    monkeypatch.setattr(publish_jobs, "LEASE", timedelta(seconds=0.6))
    entered = threading.Event()
    release = threading.Event()
    results = []

    def handle(_request):
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, json={"data": {"error_code": 0, "image": {"image_id": "cover-1"}}, "extra": {"error_code": 0}})

    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(handle))

    def execute():
        with SessionLocal() as session:
            try:
                results.append(run_due(session, adapter_override=adapter, allow_unready=True))
            except Exception as exc:
                results.append(exc)

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as observer:
            first_lease = observer.get(PublishJob, job.id).lease_until
        deadline = time.monotonic() + 5
        renewed = False
        while time.monotonic() < deadline:
            with SessionLocal() as observer:
                latest = observer.get(PublishJob, job.id).lease_until
            if latest > first_lease + timedelta(milliseconds=100):
                renewed = True
                break
            time.sleep(0.05)
        assert renewed
        time.sleep(0.7)  # 比最初的 0.6 秒租约更长，成功必须依靠独立会话续租。
        release.set()
        thread.join(10)
        assert results == [job.id]
        db.rollback(); db.refresh(job)
        assert job.phase == "cover_uploaded"
    finally:
        release.set(); thread.join(10)
        cleanup(db, user)


def test_loss_during_media_preflight_stops_before_http(client, db, monkeypatch):
    user, _campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    from app.integrations.publish import douyin
    original = douyin._cover_type
    entered = threading.Event()
    release = threading.Event()
    requests = []
    results = []

    def paused(payload):
        entered.set()
        assert release.wait(10)
        return original(payload)

    monkeypatch.setattr(douyin, "_cover_type", paused)
    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(lambda request: requests.append(request)))

    def execute():
        with SessionLocal() as session:
            try:
                results.append(run_due(session, adapter_override=adapter, allow_unready=True))
            except Exception as exc:
                results.append(exc)

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as changer:
            claimed = changer.get(PublishJob, job.id)
            claimed.claim_token = "claim-stolen-after-preflight-start"
            changer.commit()
        release.set()
        thread.join(10)
        assert results == [job.id]
        assert requests == []
        db.rollback(); db.refresh(job)
        assert job.cover_image_id is None
    finally:
        release.set(); thread.join(10)
        cleanup(db, user)


def test_disconnect_during_media_preflight_stops_http_and_preserves_checkpoint(client, db, monkeypatch):
    user, _campaign_id, connection, job = _scheduled(client, db, monkeypatch)
    from app.integrations.publish import douyin
    original = douyin._cover_type
    entered, release = threading.Event(), threading.Event()
    requests, results = [], []

    def paused(payload):
        entered.set()
        assert release.wait(10)
        return original(payload)

    monkeypatch.setattr(douyin, "_cover_type", paused)
    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(lambda request: requests.append(request)))

    def execute():
        with SessionLocal() as session:
            try:
                results.append(run_due(session, adapter_override=adapter, allow_unready=True))
            except Exception as exc:
                results.append(exc)

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as changer:
            disconnect(changer, owner_id=user.id, connection_id=connection.id)
        release.set(); thread.join(10)
        assert results == [job.id] and requests == []
        db.rollback(); db.refresh(job)
        assert job.status == "submitting" and job.phase == "pending_cover" and job.claim_token is None
        assert run_due(db, adapter_override=adapter, allow_unready=True) is None
    finally:
        release.set(); thread.join(10)
        cleanup(db, user)


def test_disconnect_after_http_starts_records_result_but_stops_next_stage(client, db, monkeypatch):
    user, _campaign_id, connection, job = _scheduled(client, db, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    requests, results = [], []

    def handle(request):
        requests.append(request)
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, json={"data": {"error_code": 0, "image": {"image_id": "cover-inflight"}},
                                          "extra": {"error_code": 0}})

    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(handle))

    def execute():
        with SessionLocal() as session:
            try:
                results.append(run_due(session, adapter_override=adapter, allow_unready=True))
            except Exception as exc:
                results.append(exc)

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as changer:
            disconnect(changer, owner_id=user.id, connection_id=connection.id)
        release.set(); thread.join(10)
        assert results == [job.id] and len(requests) == 1
        db.rollback(); db.refresh(job)
        assert job.status == "uploaded" and job.phase == "cover_uploaded" and job.cover_image_id == "cover-inflight"
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        assert len(requests) == 1
        db.rollback(); db.refresh(job)
        assert job.phase == "cover_uploaded" and job.status in {"uploaded", "submitting"}
    finally:
        release.set(); thread.join(10)
        cleanup(db, user)


def test_expiry_during_media_preflight_stops_before_http(client, db, monkeypatch):
    user, _campaign_id, connection, job = _scheduled(client, db, monkeypatch)
    from app.integrations.publish import douyin
    original = douyin._cover_type
    entered, release = threading.Event(), threading.Event()
    requests, results = [], []

    def paused(payload):
        entered.set()
        assert release.wait(10)
        return original(payload)

    monkeypatch.setattr(douyin, "_cover_type", paused)
    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(lambda request: requests.append(request)))

    def execute():
        with SessionLocal() as session:
            try:
                results.append(run_due(session, adapter_override=adapter, allow_unready=True))
            except Exception as exc:
                results.append(exc)

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as changer:
            account = changer.get(type(connection), connection.id)
            account.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            changer.commit()
        release.set(); thread.join(10)
        assert results == [job.id] and requests == []
        db.rollback(); db.refresh(job)
        assert job.status == "submitting" and job.phase == "pending_cover" and job.claim_token is None
    finally:
        release.set(); thread.join(10)
        cleanup(db, user)


def test_managed_oauth_secret_publishes_verified_cover_through_real_service(client, db, monkeypatch):
    from app.services.publish_oauth import start_authorization, finish_authorization
    from app.integrations.publish.douyin_oauth import DouyinOAuthClient
    from tests.test_publish_oauth import _settings, _tokens
    from app.models.publish_oauth import PublishOAuthSecret

    _settings(monkeypatch)
    _media(monkeypatch)
    user, campaign_id = _arm(client, db)
    requests = []
    oauth = DouyinOAuthClient(transport=httpx.MockTransport(lambda _request: _tokens(open_id="managed-open-id")))
    try:
        url = start_authorization(db, owner_id=user.id, session_cookie="isolated-session")
        state = parse_qs(urlsplit(url).query)["state"][0]
        managed = finish_authorization(db, owner_id=user.id, session_cookie="isolated-session",
                                       state=state, code="isolated-code", client=oauth)
        response = _post(client, campaign_id, connection_id=managed.id)
        assert response.status_code == 200, response.text
        job = db.get(PublishJob, response.json()["id"])
        _due(db, job)

        def upload(request):
            requests.append(request)
            assert request.headers["access-token"] == "synthetic-access-secret"
            assert request.url.params["open_id"] == "managed-open-id"
            assert COVER in request.content
            return httpx.Response(200, json={"data": {"error_code": 0, "image": {"image_id": "managed-cover"}},
                                              "extra": {"error_code": 0}})

        adapter = DouyinPublishAdapter(transport=httpx.MockTransport(upload))
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        db.rollback(); db.refresh(job)
        assert job.phase == "cover_uploaded" and job.cover_image_id == "managed-cover"
        assert len(requests) == 1
        secret = db.query(PublishOAuthSecret).filter(PublishOAuthSecret.connection_id == managed.id).one()
        assert b"synthetic-access-secret" not in secret.access_ciphertext
    finally:
        cleanup(db, user)


def test_scheduled_needs_reconfirm_is_not_auto_released_after_credentials_restore(client, db, monkeypatch):
    user, _campaign_id, connection, job = _scheduled(client, db, monkeypatch)
    monkeypatch.setenv("PUBLISH_LIVE", "1")
    requests = []
    try:
        connection.scope_set = []
        db.commit()
        assert run_due(db) is None
        db.rollback(); db.refresh(job)
        assert job.status == "needs_reconfirm"
        connection.scope_set = ["video.create.bind"]
        db.commit()
        assert run_due(db, adapter_override=DouyinPublishAdapter(transport=httpx.MockTransport(
            lambda request: requests.append(request))), allow_unready=True) is None
        assert requests == []
        db.rollback(); db.refresh(job)
        assert job.status == "needs_reconfirm"
    finally:
        cleanup(db, user)


def test_loss_during_inflight_http_does_not_save_artifact_or_continue(client, db, monkeypatch):
    user, _campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    requests = []
    results = []

    def handle(request):
        requests.append(request)
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, json={"data": {"error_code": 0, "image": {"image_id": "late-cover"}}, "extra": {"error_code": 0}})

    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(handle))

    def execute():
        with SessionLocal() as session:
            try:
                results.append(run_due(session, adapter_override=adapter, allow_unready=True))
            except Exception as exc:
                results.append(exc)

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as changer:
            claimed = changer.get(PublishJob, job.id)
            claimed.claim_token = "stolen-during-http"
            changer.commit()
        release.set()
        thread.join(10)
        assert not thread.is_alive() and results == [job.id]
        db.rollback(); db.refresh(job)
        assert job.cover_image_id is None and job.phase == "cover_intent"
        assert run_due(db, adapter_override=adapter, allow_unready=True) is None
        assert len(requests) == 1
    finally:
        release.set(); thread.join(10)
        cleanup(db, user)


@pytest.mark.parametrize(("response", "expected_status", "expected_code"), [
    (401, "failed", "auth_expired"),
    (429, "publish_unknown", "cover_rate_unknown"),
    (28001008, "failed", "auth_expired"),
    (2100004, "publish_unknown", "cover_server_unknown"),
])
def test_service_persists_safe_http_error_without_token(
    client, db, monkeypatch, caplog, response, expected_status, expected_code,
):
    user, campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    requests = []

    def handle(request):
        requests.append(request)
        if response < 1000:
            return httpx.Response(response, json={"description": "synthetic-stage-token"})
        return httpx.Response(200, json={
            "data": {"error_code": response, "description": "synthetic-stage-token"},
            "extra": {"error_code": 0, "logid": "synthetic-stage-token"},
        })

    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(handle))
    try:
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        db.rollback(); db.refresh(job)
        assert job.status == expected_status and job.error_code == expected_code
        assert len(requests) == 1
        public = client.get(f"/api/campaigns/{campaign_id}/publish")
        assert public.status_code == 200, public.text
        assert "synthetic-stage-token" not in json.dumps(public.json(), ensure_ascii=False)
        assert "synthetic-stage-token" not in caplog.text
    finally:
        cleanup(db, user)


def test_create_timeout_is_persisted_unknown_and_not_recreated(client, db, monkeypatch):
    user, _campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("upload_image/"):
            return httpx.Response(200, json={"data": {"error_code": 0, "image": {"image_id": "cover-1"}}, "extra": {"error_code": 0}})
        if request.url.path.endswith("upload_video/"):
            return httpx.Response(200, json={"data": {"error_code": 0, "video": {"video_id": "video-1"}}, "extra": {"error_code": 0}})
        raise httpx.ReadTimeout("synthetic-stage-token", request=request)

    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(handle))
    try:
        for _ in range(3):
            assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        db.rollback(); db.refresh(job)
        assert job.status == "publish_unknown" and job.error_code == "create_timeout"
        assert job.content_item_id is None
        assert run_due(db, adapter_override=adapter, allow_unready=True) is None
        assert len(requests) == 3
        assert "synthetic-stage-token" not in str(job.error_message)
    finally:
        cleanup(db, user)


def test_cover_timeout_has_separate_unknown_reason_and_never_reuploads(client, db, monkeypatch):
    user, _campaign_id, _connection, job = _scheduled(client, db, monkeypatch)
    requests = []

    def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("synthetic-stage-token", request=request)

    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(timeout))
    try:
        assert run_due(db, adapter_override=adapter, allow_unready=True) == job.id
        db.rollback(); db.refresh(job)
        assert job.status == "publish_unknown" and job.error_code == "cover_timeout"
        assert job.cover_image_id is None
        assert run_due(db, adapter_override=adapter, allow_unready=True) is None
        assert len(requests) == 1
    finally:
        cleanup(db, user)
