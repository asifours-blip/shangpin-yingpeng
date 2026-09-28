"""5C 授权链：先让真实路由失败，再以隔离 OAuth transport 驱动。"""

import base64
import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select, text

from tests.conftest import as_user, cleanup, make_user
from app.core.config import settings
from app.core.db import SessionLocal
from app.integrations.publish.douyin_oauth import DouyinOAuthClient, OAuthError
from app.models.publish_oauth import PublishOAuthSecret, PublishOAuthState
from app.models.source import PlatformConnection
from app.services.publish_oauth import disconnect, refresh_connection
from app.services.publish_oauth import finish_authorization, start_authorization
from app.integrations.publish.oauth_crypto import decrypt_token


def _settings(monkeypatch):
    monkeypatch.setenv("DOUYIN_OAUTH_ENABLED", "1")
    monkeypatch.setenv("DOUYIN_CLIENT_KEY", "isolated-app")
    monkeypatch.setenv("DOUYIN_CLIENT_SECRET", "synthetic-client-secret")
    monkeypatch.setenv("DOUYIN_REDIRECT_URI", "https://example.test/api/publish-accounts/douyin/callback")
    monkeypatch.setenv("DOUYIN_OAUTH_FRONTEND_ORIGIN", "http://127.0.0.1:5173")
    monkeypatch.setenv("DOUYIN_OAUTH_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("DOUYIN_OAUTH_KEYS", json.dumps({"v1": base64.urlsafe_b64encode(b"k" * 32).decode()}))


def _oauth(handler):
    return DouyinOAuthClient(transport=httpx.MockTransport(handler))


def _tokens(open_id="account-a", *, refresh=True, scope="video.create.bind"):
    body = {"error_code": 0, "access_token": "synthetic-access-secret", "open_id": open_id,
            "scope": scope, "expires_in": 3600, "refresh_expires_in": 7200}
    if refresh:
        body["refresh_token"] = "synthetic-refresh-secret"
    return httpx.Response(200, json={"data": body, "extra": {"error_code": 0}})


def _state(client, *, connection_id=None):
    response = client.post("/api/publish-accounts/douyin/start",
                           json={"connection_id": connection_id} if connection_id else {})
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]


def _callback(client, state, code="synthetic-code"):
    return client.get("/api/publish-accounts/douyin/callback", params={"state": state, "code": code},
                      follow_redirects=False)


def _attempt(owner_id, *, connection_id=None):
    with SessionLocal() as session:
        url = start_authorization(session, owner_id=owner_id, session_cookie="session-a",
                                  connection_id=connection_id)
    return parse_qs(urlsplit(url).query)["state"][0]


def _exchange(owner_id, state, *, code, response):
    with SessionLocal() as session:
        return finish_authorization(session, owner_id=owner_id, session_cookie="session-a",
                                    state=state, code=code, client=_oauth(lambda _request: response))


def _distinct_tokens(open_id, access, refresh, *, scope="video.create.bind", access_seconds=3600,
                     refresh_seconds=7200):
    return httpx.Response(200, json={"data": {
        "error_code": 0, "access_token": access, "refresh_token": refresh, "open_id": open_id,
        "scope": scope, "expires_in": access_seconds, "refresh_expires_in": refresh_seconds,
    }, "extra": {"error_code": 0}})


def _credential_snapshot(owner_id, connection_id):
    with SessionLocal() as session:
        connection = session.get(PlatformConnection, connection_id)
        secret = session.scalar(select(PublishOAuthSecret).where(PublishOAuthSecret.connection_id == connection_id))
        assert connection.owner_id == owner_id and secret.owner_id == owner_id
        binding = {"owner_id": connection.owner_id, "open_id": connection.external_account_id,
                   "status": connection.status, "scopes": tuple(connection.scope_set),
                   "expires_at": connection.expires_at, "version": connection.credential_version,
                   "credential_ref": connection.credential_ref,
                   "access_expires_at": secret.access_expires_at,
                   "refresh_expires_at": secret.refresh_expires_at,
                   "access": decrypt_token(secret.key_version, secret.access_ciphertext, owner_id=owner_id,
                                           connection_id=connection_id, client_key="isolated-app",
                                           open_id=connection.external_account_id),
                   "refresh": decrypt_token(secret.key_version, secret.refresh_ciphertext, owner_id=owner_id,
                                            connection_id=connection_id, client_key="isolated-app",
                                            open_id=connection.external_account_id)}
        return binding


def test_publish_account_start_requires_real_server_state(client, db, monkeypatch):
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "synthetic-session-cookie")
    _settings(monkeypatch)
    try:
        response = client.post("/api/publish-accounts/douyin/start")
        assert response.status_code == 200, response.text
        assert response.json()["authorization_url"].startswith("https://open.douyin.com/platform/oauth/connect/")
        state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
        assert state not in str(db.scalars(select(PublishOAuthState)).all())
    finally:
        cleanup(db, user)


def test_callback_consumes_state_and_encrypts_tokens_without_public_leak(client, db, monkeypatch):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/oauth/access_token/"
        assert b"grant_type=authorization_code" in request.content
        return _tokens()

    app.dependency_overrides[get_oauth_client] = lambda: _oauth(handler)
    try:
        state = _state(client)
        response = _callback(client, state)
        assert response.status_code == 303 and response.headers["location"] == "http://127.0.0.1:5173/publish-accounts?result=connected"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert len(requests) == 1
        db.rollback()
        account = db.scalar(select(PlatformConnection).where(PlatformConnection.owner_id == user.id))
        secret = db.scalar(select(PublishOAuthSecret).where(PublishOAuthSecret.connection_id == account.id))
        assert account.status == "connected" and account.credential_ref == f"db:{secret.id}"
        assert b"synthetic-access-secret" not in secret.access_ciphertext
        assert b"synthetic-refresh-secret" not in secret.refresh_ciphertext
        assert "synthetic-access-secret" not in json.dumps(client.get("/api/publish-accounts").json())
        assert _callback(client, state).status_code == 400
        assert len(requests) == 1
    finally:
        cleanup(db, user)


def test_state_cross_user_expired_and_declined_never_exchange(client, db, monkeypatch):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    first, second = make_user(db), make_user(db)
    requests = []
    app.dependency_overrides[get_oauth_client] = lambda: _oauth(lambda request: requests.append(request) or _tokens())
    as_user(first)
    client.cookies.set(settings.SESSION_COOKIE, "first-session")
    try:
        state = _state(client)
        as_user(second)
        client.cookies.set(settings.SESSION_COOKIE, "second-session")
        assert _callback(client, state).status_code == 400
        as_user(first)
        client.cookies.set(settings.SESSION_COOKIE, "first-session")
        state_row = db.get(PublishOAuthState, hashlib.sha256(state.encode()).hexdigest())
        state_row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert _callback(client, state).status_code == 400
        denied = _state(client)
        response = client.get("/api/publish-accounts/douyin/callback", params={"state": denied}, follow_redirects=False)
        assert response.status_code == 303 and "authorization_declined" in response.headers["location"]
        assert _callback(client, denied).status_code == 400
        assert requests == []
    finally:
        cleanup(db, first, second)


@pytest.mark.parametrize("response", [
    httpx.ReadTimeout("synthetic-code"),
    httpx.Response(200, json={"data": {"error_code": 0, "access_token": "token"}, "extra": {"error_code": 0}}),
    _tokens(scope="other.scope"),
])
def test_exchange_unknown_invalid_and_missing_scope_do_not_connect(client, db, monkeypatch, response):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    requests = []

    def handler(request):
        requests.append(request)
        if isinstance(response, Exception):
            raise response
        return response

    app.dependency_overrides[get_oauth_client] = lambda: _oauth(handler)
    try:
        state = _state(client)
        result = _callback(client, state)
        assert result.status_code == 303
        assert "connected" not in result.headers["location"]
        assert "synthetic-code" not in result.headers["location"]
        assert _callback(client, state).status_code == 400
        assert len(requests) == 1
        db.rollback()
        assert db.scalar(select(PlatformConnection).where(PlatformConnection.owner_id == user.id)) is None
    finally:
        cleanup(db, user)


def test_repeat_account_updates_same_connection_and_new_open_id_keeps_old(client, db, monkeypatch):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    open_id = ["account-a"]
    app.dependency_overrides[get_oauth_client] = lambda: _oauth(lambda _request: _tokens(open_id[0]))
    try:
        assert _callback(client, _state(client)).status_code == 303
        db.rollback()
        first = db.scalar(select(PlatformConnection).where(PlatformConnection.owner_id == user.id))
        first_id, version = first.id, first.credential_version
        assert _callback(client, _state(client, connection_id=first_id)).status_code == 303
        db.rollback(); db.refresh(first)
        assert first.id == first_id and first.credential_version == version + 1
        open_id[0] = "account-b"
        assert _callback(client, _state(client, connection_id=first_id)).status_code == 303
        db.rollback()
        rows = db.scalars(select(PlatformConnection).where(PlatformConnection.owner_id == user.id).order_by(PlatformConnection.id)).all()
        assert [(row.id, row.external_account_id) for row in rows] == [(first_id, "account-a"), (first_id + 1, "account-b")]
        assert all(row.status == "connected" for row in rows)
    finally:
        cleanup(db, user)


def test_older_global_callback_cannot_overwrite_newer_reauthorization_of_same_account(db, monkeypatch):
    _settings(monkeypatch)
    user = make_user(db)
    owner_id = user.id
    entered, release = threading.Event(), threading.Event()
    results = []
    try:
        first = _exchange(owner_id, _attempt(owner_id), code="initial-a",
                          response=_distinct_tokens("account-a", "access-initial", "refresh-initial"))
        connection_id = first.id
        global_state = _attempt(owner_id)

        def old_exchange(_request):
            entered.set()
            assert release.wait(10)
            return _distinct_tokens("account-a", "access-global-old", "refresh-global-old",
                                    scope="video.create.bind,old.scope", access_seconds=1800)

        def finish_old():
            try:
                with SessionLocal() as session:
                    finish_authorization(session, owner_id=owner_id, session_cookie="session-a",
                                         state=global_state, code="code-global-old", client=_oauth(old_exchange))
                results.append("accepted")
            except OAuthError as exc:
                results.append(exc.code)

        thread = threading.Thread(target=finish_old)
        thread.start()
        assert entered.wait(10)
        _exchange(owner_id, _attempt(owner_id, connection_id=connection_id), code="code-new-a",
                  response=_distinct_tokens("account-a", "access-new-a", "refresh-new-a",
                                            scope="video.create.bind,new.scope", refresh_seconds=3600))
        expected = _credential_snapshot(owner_id, connection_id)
        release.set(); thread.join(10)
        assert not thread.is_alive() and results == ["authorization_stale"]
        assert _credential_snapshot(owner_id, connection_id) == expected
        assert expected["access"] == "access-new-a" and expected["refresh"] == "refresh-new-a"
        assert expected["scopes"] == ("video.create.bind", "new.scope") and expected["status"] == "connected"
    finally:
        release.set()
        cleanup(db, user)


def test_old_global_callback_stays_stale_after_disconnect_and_new_reauthorization(db, monkeypatch):
    _settings(monkeypatch)
    user = make_user(db)
    owner_id = user.id
    entered, release = threading.Event(), threading.Event()
    results = []
    try:
        first = _exchange(owner_id, _attempt(owner_id), code="initial-a",
                          response=_distinct_tokens("account-a", "access-initial", "refresh-initial"))
        connection_id = first.id
        global_state = _attempt(owner_id)

        def old_exchange(_request):
            entered.set()
            assert release.wait(10)
            return _distinct_tokens("account-a", "access-before-disconnect", "refresh-before-disconnect",
                                    scope="video.create.bind,old.scope", refresh_seconds=1800)

        def finish_old():
            try:
                with SessionLocal() as session:
                    finish_authorization(session, owner_id=owner_id, session_cookie="session-a",
                                         state=global_state, code="code-before-disconnect",
                                         client=_oauth(old_exchange))
                results.append("accepted")
            except OAuthError as exc:
                results.append(exc.code)

        thread = threading.Thread(target=finish_old)
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as session:
            disconnect(session, owner_id=owner_id, connection_id=connection_id)
        _exchange(owner_id, _attempt(owner_id, connection_id=connection_id), code="code-after-disconnect",
                  response=_distinct_tokens("account-a", "access-restored", "refresh-restored",
                                            scope="video.create.bind,restored.scope", refresh_seconds=5400))
        expected = _credential_snapshot(owner_id, connection_id)
        release.set(); thread.join(10)
        assert not thread.is_alive() and results == ["authorization_stale"]
        assert _credential_snapshot(owner_id, connection_id) == expected
        assert expected["access"] == "access-restored" and expected["refresh"] == "refresh-restored"
        assert expected["scopes"] == ("video.create.bind", "restored.scope") and expected["status"] == "connected"
    finally:
        release.set()
        cleanup(db, user)


def test_old_callback_started_from_a_cannot_overwrite_newer_authorization_of_final_b(db, monkeypatch):
    _settings(monkeypatch)
    user = make_user(db)
    owner_id = user.id
    entered, release = threading.Event(), threading.Event()
    results = []
    try:
        account_a = _exchange(owner_id, _attempt(owner_id), code="initial-a",
                              response=_distinct_tokens("account-a", "access-a", "refresh-a"))
        account_b = _exchange(owner_id, _attempt(owner_id), code="initial-b",
                              response=_distinct_tokens("account-b", "access-b", "refresh-b"))
        old_state_from_a = _attempt(owner_id, connection_id=account_a.id)
        original_a = _credential_snapshot(owner_id, account_a.id)

        def old_exchange(_request):
            entered.set()
            assert release.wait(10)
            return _distinct_tokens("account-b", "access-old-b", "refresh-old-b",
                                    scope="video.create.bind,old.scope", access_seconds=1200)

        def finish_old():
            try:
                with SessionLocal() as session:
                    finish_authorization(session, owner_id=owner_id, session_cookie="session-a",
                                         state=old_state_from_a, code="code-old-b", client=_oauth(old_exchange))
                results.append("accepted")
            except OAuthError as exc:
                results.append(exc.code)

        thread = threading.Thread(target=finish_old)
        thread.start()
        assert entered.wait(10)
        _exchange(owner_id, _attempt(owner_id, connection_id=account_b.id), code="code-new-b",
                  response=_distinct_tokens("account-b", "access-new-b", "refresh-new-b",
                                            scope="video.create.bind,new.scope", refresh_seconds=4800))
        expected_b = _credential_snapshot(owner_id, account_b.id)
        release.set(); thread.join(10)
        assert not thread.is_alive() and results == ["authorization_stale"]
        assert _credential_snapshot(owner_id, account_b.id) == expected_b
        assert _credential_snapshot(owner_id, account_a.id) == original_a
        assert expected_b["access"] == "access-new-b" and expected_b["refresh"] == "refresh-new-b"
        assert expected_b["scopes"] == ("video.create.bind", "new.scope") and expected_b["status"] == "connected"
    finally:
        release.set()
        cleanup(db, user)


def test_stale_callback_redirects_safely_and_consumed_code_is_not_exchanged_again(client, db, monkeypatch):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    exchanges = []

    def handler(request):
        code = parse_qs(request.content.decode())["code"][0]
        exchanges.append(code)
        return _distinct_tokens("account-a", f"access-{code}", f"refresh-{code}")

    app.dependency_overrides[get_oauth_client] = lambda: _oauth(handler)
    try:
        assert "result=connected" in _callback(client, _state(client), code="initial").headers["location"]
        db.rollback()
        connection = db.scalar(select(PlatformConnection).where(PlatformConnection.owner_id == user.id))
        old_state = _state(client)
        new_state = _state(client, connection_id=connection.id)
        assert "result=connected" in _callback(client, new_state, code="new").headers["location"]
        stale = _callback(client, old_state, code="old")
        assert stale.status_code == 303
        assert "result=authorization_stale" in stale.headers["location"]
        assert "connected" not in stale.headers["location"]
        assert not any(value in stale.headers["location"] for value in ("access-old", "refresh-old", old_state))
        count = len(exchanges)
        assert _callback(client, old_state, code="old").status_code == 400
        assert exchanges == ["initial", "new", "old"] and len(exchanges) == count
    finally:
        cleanup(db, user)


def test_concurrent_callbacks_same_account_have_one_connection(client, db, monkeypatch):
    _settings(monkeypatch)
    user = make_user(db)
    entered = threading.Barrier(2)
    results = []

    def handler(_request):
        entered.wait(timeout=10)
        return _tokens()

    oauth = _oauth(handler)
    try:
        with SessionLocal() as session:
            first = parse_qs(urlsplit(start_authorization(session, owner_id=user.id, session_cookie="session-a")).query)["state"][0]
            second = parse_qs(urlsplit(start_authorization(session, owner_id=user.id, session_cookie="session-a")).query)["state"][0]

        def finish(state):
            try:
                with SessionLocal() as session:
                    row = finish_authorization(session, owner_id=user.id, session_cookie="session-a",
                                               state=state, code="code-" + state[:6], client=oauth)
                    results.append(row.id)
            except Exception as exc:
                results.append(exc)

        threads = [threading.Thread(target=finish, args=(state,)) for state in (first, second)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10)
        assert all(not thread.is_alive() for thread in threads)
        successes = [item for item in results if isinstance(item, int)]
        errors = [item.code for item in results if isinstance(item, OAuthError)]
        assert len(results) == 2 and len(successes) + len(errors) == 2, results
        assert successes and len(set(successes)) == 1, results
        assert all(code == "authorization_stale" for code in errors), results
        db.rollback()
        assert len(db.scalars(select(PlatformConnection).where(PlatformConnection.owner_id == user.id)).all()) == 1
    finally:
        cleanup(db, user)


def test_reflected_token_in_open_id_or_scope_never_reaches_db_or_api(client, db, monkeypatch, caplog):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    cases = [
        httpx.Response(200, json={"data": {"error_code": 0, "access_token": "synthetic-access-secret",
            "refresh_token": "synthetic-refresh-secret", "open_id": "synthetic-access-secret",
            "scope": "video.create.bind", "expires_in": 3600, "refresh_expires_in": 7200}}),
        _tokens(scope="video.create.bind,synthetic-refresh-secret"),
    ]
    try:
        for response in cases:
            app.dependency_overrides[get_oauth_client] = lambda response=response: _oauth(lambda _request: response)
            assert "response_invalid" in _callback(client, _state(client)).headers["location"]
        db.rollback()
        assert db.scalar(select(PlatformConnection).where(PlatformConnection.owner_id == user.id)) is None
        payload = json.dumps(client.get("/api/publish-accounts").json()) + caplog.text
        assert "synthetic-access-secret" not in payload and "synthetic-refresh-secret" not in payload
    finally:
        cleanup(db, user)


def test_refresh_claim_serializes_and_key_rotation_keeps_original_refresh_expiry(client, db, monkeypatch):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    app.dependency_overrides[get_oauth_client] = lambda: _oauth(lambda _request: _tokens())
    entered, release = threading.Event(), threading.Event()
    results = []

    def slow(_request):
        entered.set()
        assert release.wait(10)
        return _tokens(refresh=False)

    try:
        assert _callback(client, _state(client)).status_code == 303
        db.rollback()
        connection = db.scalar(select(PlatformConnection).where(PlatformConnection.owner_id == user.id))
        connection_id = connection.id
        old = db.scalar(select(PublishOAuthSecret).where(PublishOAuthSecret.connection_id == connection_id))
        original_refresh_expiry = old.refresh_expires_at
        monkeypatch.setenv("DOUYIN_OAUTH_KEYS", json.dumps({
            "v1": base64.urlsafe_b64encode(b"k" * 32).decode(),
            "v2": base64.urlsafe_b64encode(b"z" * 32).decode(),
        }))
        monkeypatch.setenv("DOUYIN_OAUTH_ACTIVE_KEY_VERSION", "v2")

        def first():
            try:
                with SessionLocal() as session:
                    results.append(refresh_connection(session, owner_id=user.id, connection_id=connection_id,
                                                      client=_oauth(slow), force=True))
            except Exception as exc:
                results.append(exc)

        thread = threading.Thread(target=first)
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as session:
            assert refresh_connection(session, owner_id=user.id, connection_id=connection_id,
                                      client=_oauth(lambda _request: pytest.fail("duplicate refresh")), force=True) == "busy"
        release.set(); thread.join(10)
        assert results == ["refreshed"]
        db.rollback()
        updated = db.scalar(select(PublishOAuthSecret).where(PublishOAuthSecret.connection_id == connection_id))
        assert updated.key_version == "v2" and updated.refresh_expires_at == original_refresh_expiry
        assert decrypt_token(updated.key_version, updated.refresh_ciphertext, owner_id=user.id,
                             connection_id=connection_id, client_key="isolated-app", open_id="account-a") == "synthetic-refresh-secret"
    finally:
        release.set()
        cleanup(db, user)


def test_refresh_reported_remaining_time_can_shorten_but_not_extend_deadline(client, db, monkeypatch):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    app.dependency_overrides[get_oauth_client] = lambda: _oauth(lambda _request: _tokens())
    try:
        assert _callback(client, _state(client)).status_code == 303
        db.rollback()
        connection = db.scalar(select(PlatformConnection).where(PlatformConnection.owner_id == user.id))
        secret = db.scalar(select(PublishOAuthSecret).where(PublishOAuthSecret.connection_id == connection.id))
        original = secret.refresh_expires_at

        def shortened(_request):
            response = _tokens(refresh=False).json()
            response["data"]["refresh_expires_in"] = 120
            return httpx.Response(200, json=response)

        with SessionLocal() as session:
            assert refresh_connection(session, owner_id=user.id, connection_id=connection.id,
                                      client=_oauth(shortened), force=True) == "refreshed"
        db.rollback(); db.refresh(secret)
        shortened_deadline = secret.refresh_expires_at
        assert shortened_deadline < original
        assert shortened_deadline > datetime.now(timezone.utc) + timedelta(seconds=100)

        def longer(_request):
            response = _tokens(refresh=False).json()
            response["data"]["refresh_expires_in"] = 999999
            return httpx.Response(200, json=response)

        with SessionLocal() as session:
            assert refresh_connection(session, owner_id=user.id, connection_id=connection.id,
                                      client=_oauth(longer), force=True) == "refreshed"
        db.rollback(); db.refresh(secret)
        assert secret.refresh_expires_at == shortened_deadline
    finally:
        cleanup(db, user)


def test_disconnect_during_refresh_discards_late_token(client, db, monkeypatch):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    app.dependency_overrides[get_oauth_client] = lambda: _oauth(lambda _request: _tokens())
    entered, release = threading.Event(), threading.Event()
    results = []

    def slow(_request):
        entered.set()
        assert release.wait(10)
        return _tokens(refresh=False)

    try:
        assert _callback(client, _state(client)).status_code == 303
        db.rollback()
        account = db.scalar(select(PlatformConnection).where(PlatformConnection.owner_id == user.id))
        account_id = account.id

        def first():
            try:
                with SessionLocal() as session:
                    results.append(refresh_connection(session, owner_id=user.id, connection_id=account_id,
                                                      client=_oauth(slow), force=True))
            except Exception as exc:
                results.append(exc)

        thread = threading.Thread(target=first)
        thread.start()
        assert entered.wait(10)
        with SessionLocal() as session:
            disconnect(session, owner_id=user.id, connection_id=account_id)
        release.set(); thread.join(10)
        assert results == ["stale"]
        db.rollback(); db.refresh(account)
        assert account.status == "revoked" and account.credential_ref is None
        assert db.scalar(select(PublishOAuthSecret).where(PublishOAuthSecret.connection_id == account_id)) is None
    finally:
        release.set()
        cleanup(db, user)


def test_state_expiry_is_rechecked_after_waiting_for_row_lock(client, db, monkeypatch):
    _settings(monkeypatch)
    user = make_user(db)
    state_url = start_authorization(db, owner_id=user.id, session_cookie="session-a")
    state = parse_qs(urlsplit(state_url).query)["state"][0]
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    db.get(PublishOAuthState, state_hash).expires_at = datetime.now(timezone.utc) + timedelta(milliseconds=650)
    db.commit()
    holder = SessionLocal()
    entered = threading.Event()
    results, requests = [], []
    try:
        holder.execute(text("SET LOCAL lock_timeout = '8s'"))
        holder.scalar(select(PublishOAuthState.state_hash).where(
            PublishOAuthState.state_hash == state_hash,
        ).with_for_update())

        def consume():
            try:
                with SessionLocal() as session:
                    session.execute(text("SET application_name = 'oauth-state-wait-test'"))
                    entered.set()
                    finish_authorization(session, owner_id=user.id, session_cookie="session-a", state=state,
                                         code="code-once", client=_oauth(lambda request: requests.append(request) or _tokens()))
                    results.append("accepted")
            except OAuthError as exc:
                results.append(exc.code)

        thread = threading.Thread(target=consume)
        thread.start()
        assert entered.wait(10)
        deadline = time.monotonic() + 5
        waited = False
        while time.monotonic() < deadline:
            with SessionLocal() as observer:
                waited = bool(observer.scalar(text("SELECT 1 FROM pg_stat_activity WHERE application_name = 'oauth-state-wait-test' AND wait_event_type = 'Lock' LIMIT 1")))
            if waited:
                break
            time.sleep(0.02)
        assert waited
        time.sleep(0.75)
        holder.commit()
        thread.join(10)
        assert not thread.is_alive() and results == ["state_invalid"] and requests == []
    finally:
        holder.rollback(); holder.close()
        cleanup(db, user)


def test_callback_access_log_filter_removes_code_and_state():
    from app.api.publish_accounts import _RedactOAuthCallback

    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1", "GET", "/api/publish-accounts/douyin/callback?code=secret-code&state=secret-state", "1.1", 303),
                               None)
    assert _RedactOAuthCallback().filter(record)
    rendered = record.getMessage()
    assert "secret-code" not in rendered and "secret-state" not in rendered
    assert "/api/publish-accounts/douyin/callback" in rendered


def test_invalid_callback_configuration_is_visible_before_start(client, db, monkeypatch):
    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    try:
        monkeypatch.setenv("DOUYIN_REDIRECT_URI", "https://user:secret@example.test:bad/api/publish-accounts/douyin/callback?code=unsafe")
        listed = client.get("/api/publish-accounts").json()
        assert any("DOUYIN_REDIRECT_URI" in item for item in listed["douyin"]["configuration_missing"])
        assert client.post("/api/publish-accounts/douyin/start").status_code == 503
        db.rollback()
        assert db.scalar(select(PublishOAuthState).where(PublishOAuthState.owner_id == user.id)) is None
    finally:
        cleanup(db, user)


def test_key_disappears_after_exchange_state_consumed_but_connection_not_false_connected(client, db, monkeypatch):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    state = _state(client)

    def handler(_request):
        monkeypatch.delenv("DOUYIN_OAUTH_KEYS")
        return _tokens()

    app.dependency_overrides[get_oauth_client] = lambda: _oauth(handler)
    try:
        response = _callback(client, state)
        assert response.status_code == 303 and "encryption_unavailable" in response.headers["location"]
        assert _callback(client, state).status_code == 503  # 配置已撤，不能再次换同一个 code。
        db.rollback()
        assert db.scalar(select(PlatformConnection).where(PlatformConnection.owner_id == user.id)) is None
        assert db.scalar(select(PublishOAuthSecret).where(PublishOAuthSecret.owner_id == user.id)) is None
    finally:
        cleanup(db, user)


def test_refresh_success_response_missing_token_is_unknown_and_not_retried(client, db, monkeypatch):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    app.dependency_overrides[get_oauth_client] = lambda: _oauth(lambda _request: _tokens())
    calls = []
    try:
        assert _callback(client, _state(client)).status_code == 303
        db.rollback()
        account_id = db.scalar(select(PlatformConnection.id).where(PlatformConnection.owner_id == user.id))

        def broken(request):
            calls.append(request)
            return httpx.Response(200, json={"data": {"error_code": 0, "open_id": "account-a",
                                               "scope": "video.create.bind", "expires_in": 3600}})

        with SessionLocal() as session:
            with pytest.raises(OAuthError) as error:
                refresh_connection(session, owner_id=user.id, connection_id=account_id,
                                   client=_oauth(broken), force=True)
            assert error.value.code == "response_invalid"
        with SessionLocal() as session:
            assert refresh_connection(session, owner_id=user.id, connection_id=account_id,
                                      client=_oauth(lambda _request: pytest.fail("must not reuse refresh")), force=True) == "busy"
        with SessionLocal() as session:
            secret = session.scalar(select(PublishOAuthSecret).where(PublishOAuthSecret.connection_id == account_id))
            secret.refresh_claim_until = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
        with SessionLocal() as session:
            with pytest.raises(OAuthError) as error:
                refresh_connection(session, owner_id=user.id, connection_id=account_id,
                                   client=_oauth(lambda _request: pytest.fail("must not reuse refresh")), force=True)
            assert error.value.code == "refresh_unknown"
        assert len(calls) == 1
    finally:
        cleanup(db, user)


def test_reauthorization_during_refresh_keeps_newer_token(client, db, monkeypatch):
    from app.main import app
    from app.api.publish_accounts import get_oauth_client

    _settings(monkeypatch)
    user = make_user(db)
    as_user(user)
    client.cookies.set(settings.SESSION_COOKIE, "session-a")
    app.dependency_overrides[get_oauth_client] = lambda: _oauth(lambda _request: _tokens())
    entered, release = threading.Event(), threading.Event()
    results = []

    def slow(_request):
        entered.set()
        assert release.wait(10)
        return _tokens(refresh=False)

    try:
        assert _callback(client, _state(client)).status_code == 303
        db.rollback()
        account_id = db.scalar(select(PlatformConnection.id).where(PlatformConnection.owner_id == user.id))

        def refresh():
            try:
                with SessionLocal() as session:
                    results.append(refresh_connection(session, owner_id=user.id, connection_id=account_id,
                                                      client=_oauth(slow), force=True))
            except Exception as exc:
                results.append(exc)

        thread = threading.Thread(target=refresh)
        thread.start()
        assert entered.wait(10)
        reauth = _tokens()
        revised = reauth.json()
        revised["data"]["access_token"] = "newer-reauthorized-access"
        revised["data"]["refresh_token"] = "newer-reauthorized-refresh"
        app.dependency_overrides[get_oauth_client] = lambda: _oauth(lambda _request: httpx.Response(200, json=revised))
        assert _callback(client, _state(client, connection_id=account_id)).status_code == 303
        release.set(); thread.join(10)
        assert results == ["stale"]
        db.rollback()
        secret = db.scalar(select(PublishOAuthSecret).where(PublishOAuthSecret.connection_id == account_id))
        assert decrypt_token(secret.key_version, secret.access_ciphertext, owner_id=user.id,
                             connection_id=account_id, client_key="isolated-app", open_id="account-a") == "newer-reauthorized-access"
    finally:
        release.set()
        cleanup(db, user)
