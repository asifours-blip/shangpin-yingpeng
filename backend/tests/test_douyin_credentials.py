"""发布凭证只从部署配置读取，连接引用本身不是授权。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.integrations.publish.credentials import CredentialError, resolve_credential


def _connection(**changes):
    values = dict(
        id=19, owner_id=7, platform="douyin", purpose="publish",
        external_account_id="bound-open-id", status="connected",
        scope_set=["video.create.bind"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        credential_ref="env:CODEX_DOUYIN_TEST_SECRET",
    )
    values.update(changes)
    return SimpleNamespace(**values)


def _secret(**changes):
    values = dict(
        owner_id=7, connection_id=19, platform="douyin", purpose="publish",
        open_id="bound-open-id", scopes=["video.create.bind"],
        expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        client_key="isolated-app", access_token="synthetic-only-access-token",
    )
    values.update(changes)
    return values


def test_secret_binding_checks_owner_connection_account_scope_and_expiry(monkeypatch):
    monkeypatch.setenv("DOUYIN_CLIENT_KEY", "isolated-app")
    monkeypatch.setenv("CODEX_DOUYIN_TEST_SECRET", json.dumps(_secret()))
    credential = resolve_credential(_connection(), owner_id=7, target_open_id="bound-open-id")
    assert credential.access_token == "synthetic-only-access-token"
    assert "synthetic-only-access-token" not in repr(credential)

    cases = [
        (_connection(owner_id=8), _secret(), "wrong_owner"),
        (_connection(), _secret(owner_id=8), "wrong_owner"),
        (_connection(), _secret(connection_id=20), "wrong_connection"),
        (_connection(external_account_id="other"), _secret(), "wrong_account"),
        (_connection(), _secret(open_id="other"), "wrong_account"),
        (_connection(scope_set=[]), _secret(), "wrong_scope"),
        (_connection(), _secret(scopes=[]), "wrong_scope"),
        (_connection(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)), _secret(), "expired"),
        (_connection(), _secret(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()), "expired"),
        (_connection(), _secret(client_key="other-app"), "wrong_application"),
    ]
    for connection, secret, expected in cases:
        monkeypatch.setenv("CODEX_DOUYIN_TEST_SECRET", json.dumps(secret))
        with pytest.raises(CredentialError) as caught:
            resolve_credential(connection, owner_id=7, target_open_id="bound-open-id")
        assert caught.value.code == expected
        assert "synthetic-only-access-token" not in str(caught.value)


def test_missing_or_untrusted_reference_never_returns_a_token(monkeypatch):
    monkeypatch.setenv("DOUYIN_CLIENT_KEY", "isolated-app")
    monkeypatch.setenv("CODEX_DOUYIN_TEST_SECRET", json.dumps(_secret()))
    for reference in (None, "", "vault://publish/test", "env:OTHER-NAME", "env:CODEX_DOUYIN_TEST_SECRET;echo"):
        with pytest.raises(CredentialError):
            resolve_credential(_connection(credential_ref=reference), owner_id=7, target_open_id="bound-open-id")


def test_malformed_scopes_are_safe_configuration_errors(monkeypatch):
    monkeypatch.setenv("DOUYIN_CLIENT_KEY", "isolated-app")
    for malformed in (17, [{"unexpected": "object"}], "video.create.bind"):
        monkeypatch.setenv("CODEX_DOUYIN_TEST_SECRET", json.dumps(_secret(scopes=malformed)))
        with pytest.raises(CredentialError) as caught:
            resolve_credential(_connection(), owner_id=7, target_open_id="bound-open-id")
        assert caught.value.code == "credential_invalid"
        assert "synthetic-only-access-token" not in str(caught.value)
