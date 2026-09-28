"""发布账号授权：一次性 state、短事务落密文、版本栅栏刷新。"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.integrations.publish.credentials import CredentialError
from app.integrations.publish.douyin_oauth import DouyinOAuthClient, OAuthError, OAuthTokens, SCOPE
from app.integrations.publish.oauth_crypto import active_version, decrypt_token, encrypt_token
from app.models.publish_oauth import PublishOAuthSecret, PublishOAuthState
from app.models.source import PlatformConnection

STATE_TTL = timedelta(minutes=10)
REFRESH_CLAIM = timedelta(seconds=30)


@dataclass(frozen=True, repr=False)
class OAuthConfig:
    client_key: str
    client_secret: str = field(repr=False)
    callback_uri: str
    frontend_origin: str


def configuration_missing() -> list[str]:
    names = ("DOUYIN_CLIENT_KEY", "DOUYIN_CLIENT_SECRET", "DOUYIN_REDIRECT_URI", "DOUYIN_OAUTH_FRONTEND_ORIGIN",
             "DOUYIN_OAUTH_KEYS", "DOUYIN_OAUTH_ACTIVE_KEY_VERSION")
    missing = [name for name in names if not os.environ.get(name)]
    if "DOUYIN_OAUTH_KEYS" not in missing and "DOUYIN_OAUTH_ACTIVE_KEY_VERSION" not in missing:
        try:
            active_version()
        except CredentialError:
            missing.append("DOUYIN_OAUTH_KEYS（无有效主密钥版本）")
    callback = os.environ.get("DOUYIN_REDIRECT_URI")
    frontend = os.environ.get("DOUYIN_OAUTH_FRONTEND_ORIGIN")
    if callback and frontend:
        try:
            parsed, web = urlsplit(callback), urlsplit(frontend.rstrip("/"))
            _ = (parsed.port, web.port)  # 非法端口会抛 ValueError；标准 443/80 可省略。
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment
                    or parsed.path != "/api/publish-accounts/douyin/callback"):
                missing.append("DOUYIN_REDIRECT_URI（无效）")
            if (web.scheme not in {"https", "http"} or not web.hostname or web.username or web.password
                    or web.path or web.query or web.fragment
                    or (web.scheme == "http" and web.hostname not in {"localhost", "127.0.0.1"})):
                missing.append("DOUYIN_OAUTH_FRONTEND_ORIGIN（无效）")
        except ValueError:
            missing.extend(["DOUYIN_REDIRECT_URI（无效）", "DOUYIN_OAUTH_FRONTEND_ORIGIN（无效）"])
    return missing


def configuration() -> OAuthConfig:
    if os.environ.get("DOUYIN_OAUTH_ENABLED") != "1":
        raise OAuthError("oauth_disabled")
    if configuration_missing():
        raise OAuthError("oauth_unconfigured")
    try:
        active_version()
    except CredentialError:
        raise OAuthError("oauth_unconfigured") from None
    callback = os.environ["DOUYIN_REDIRECT_URI"]
    frontend = os.environ["DOUYIN_OAUTH_FRONTEND_ORIGIN"].rstrip("/")
    return OAuthConfig(os.environ["DOUYIN_CLIENT_KEY"], os.environ["DOUYIN_CLIENT_SECRET"], callback, frontend)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _cookie_hash(cookie: str | None) -> str:
    if not cookie:
        raise OAuthError("session_required")
    return _hash(cookie)


def start_authorization(db: Session, *, owner_id: int, session_cookie: str | None,
                        connection_id: int | None = None, client: DouyinOAuthClient | None = None) -> str:
    config = configuration()
    session_hash = _cookie_hash(session_cookie)
    connection_version = None
    if connection_id is not None:
        connection = db.get(PlatformConnection, connection_id)
        if (connection is None or connection.owner_id != owner_id or connection.platform != "douyin"
                or connection.purpose != "publish" or connection.app_client_key != config.client_key):
            raise OAuthError("account_missing")
        connection_version = connection.credential_version
    state = secrets.token_urlsafe(32)
    db.add(PublishOAuthState(
        state_hash=_hash(state), owner_id=owner_id, session_hash=session_hash,
        client_key=config.client_key, callback_uri=config.callback_uri, connection_id=connection_id,
        connection_version=connection_version, expires_at=datetime.now(timezone.utc) + STATE_TTL,
    ))
    db.commit()
    return (client or DouyinOAuthClient()).authorization_url(
        client_key=config.client_key, callback_uri=config.callback_uri, state=state,
    )


def _consume_state(db: Session, *, state: str, owner_id: int, session_cookie: str | None,
                   config: OAuthConfig) -> tuple[int | None, int | None, int]:
    if not state or len(state) > 256:
        raise OAuthError("state_invalid")
    session_hash = _cookie_hash(session_cookie)
    db.execute(text("SET LOCAL lock_timeout = '8s'"))
    locked = db.scalar(select(PublishOAuthState.state_hash).where(
        PublishOAuthState.state_hash == _hash(state),
    ).with_for_update())
    if locked is None:
        db.rollback()
        raise OAuthError("state_invalid")
    row = db.execute(
        update(PublishOAuthState).where(
            PublishOAuthState.state_hash == _hash(state), PublishOAuthState.owner_id == owner_id,
            PublishOAuthState.session_hash == session_hash, PublishOAuthState.client_key == config.client_key,
            PublishOAuthState.callback_uri == config.callback_uri,
            PublishOAuthState.consumed_at.is_(None), PublishOAuthState.expires_at > text("clock_timestamp()"),
        ).values(consumed_at=text("clock_timestamp()"))
        .returning(PublishOAuthState.connection_id, PublishOAuthState.connection_version,
                   PublishOAuthState.attempt_id)
    ).first()
    if row is None:
        db.rollback()
        raise OAuthError("state_invalid")
    db.commit()  # code 换取之前就消费；未知网络结果不会复用一次性 code。
    return row.connection_id, row.connection_version, row.attempt_id


def _account_lock(db: Session, owner_id: int, client_key: str, open_id: str) -> None:
    digest = hashlib.sha256(f"{owner_id}:{client_key}:{open_id}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _bind_tokens(db: Session, *, owner_id: int, config: OAuthConfig, tokens: OAuthTokens,
                 started_connection_id: int | None, started_version: int | None,
                 attempt_id: int) -> PlatformConnection:
    now = datetime.now(timezone.utc)
    _account_lock(db, owner_id, config.client_key, "*")
    if started_connection_id is not None:
        started = db.scalar(select(PlatformConnection).where(
            PlatformConnection.id == started_connection_id,
        ).with_for_update().execution_options(populate_existing=True))
        if (started is None or started.owner_id != owner_id or started.platform != "douyin"
                or started.purpose != "publish" or started.app_client_key != config.client_key
                or started.credential_version != started_version):
            raise OAuthError("authorization_stale")
    connection = db.scalar(select(PlatformConnection).where(
        PlatformConnection.owner_id == owner_id, PlatformConnection.platform == "douyin",
        PlatformConnection.purpose == "publish", PlatformConnection.app_client_key == config.client_key,
        PlatformConnection.external_account_id == tokens.open_id,
    ).with_for_update().execution_options(populate_existing=True))
    if connection is not None and attempt_id <= connection.last_authorization_attempt_id:
        raise OAuthError("authorization_stale")
    if connection is not None and connection.status == "revoked" and connection.id != started_connection_id:
        raise OAuthError("authorization_stale")
    if connection is None:
        connection = PlatformConnection(
            owner_id=owner_id, platform="douyin", purpose="publish", app_client_key=config.client_key,
            external_account_id=tokens.open_id, status="pending", scope_set=[], credential_version=0,
        )
        db.add(connection)
        db.flush()
    version = int(connection.credential_version or 0) + 1
    assert tokens.refresh_token is not None
    access_key, access_cipher = encrypt_token(
        tokens.access_token, owner_id=owner_id, connection_id=connection.id,
        client_key=config.client_key, open_id=tokens.open_id,
    )
    refresh_key, refresh_cipher = encrypt_token(
        tokens.refresh_token, owner_id=owner_id, connection_id=connection.id,
        client_key=config.client_key, open_id=tokens.open_id,
    )
    if refresh_key != access_key:
        raise OAuthError("encryption_unavailable")
    secret = db.scalar(select(PublishOAuthSecret).where(
        PublishOAuthSecret.connection_id == connection.id,
    ).with_for_update().execution_options(populate_existing=True))
    if secret is None:
        secret = PublishOAuthSecret(connection_id=connection.id, owner_id=owner_id,
                                    client_key=config.client_key, open_id=tokens.open_id)
        db.add(secret)
    secret.key_version = access_key
    secret.access_ciphertext = access_cipher
    secret.refresh_ciphertext = refresh_cipher
    secret.access_expires_at = now + timedelta(seconds=tokens.expires_in)
    secret.refresh_expires_at = now + timedelta(seconds=tokens.refresh_expires_in or 0)
    secret.credential_version = version
    secret.refresh_claim_token = None
    secret.refresh_claim_until = None
    secret.updated_at = now
    db.flush()
    connection.credential_ref = f"db:{secret.id}"
    connection.credential_origin = "managed"
    connection.status = "connected"
    connection.scope_set = list(tokens.scopes)
    connection.expires_at = secret.access_expires_at
    connection.credential_version = version
    connection.last_authorization_attempt_id = attempt_id
    connection.updated_at = now
    db.commit()
    db.refresh(connection)
    return connection


def finish_authorization(db: Session, *, owner_id: int, session_cookie: str | None,
                         state: str, code: str | None, client: DouyinOAuthClient | None = None) -> PlatformConnection:
    config = configuration()
    started_id, started_version, attempt_id = _consume_state(
        db, state=state, owner_id=owner_id, session_cookie=session_cookie, config=config,
    )
    if not code or len(code) > 2048:
        raise OAuthError("authorization_declined")
    try:
        tokens = (client or DouyinOAuthClient()).exchange(
            client_key=config.client_key, client_secret=config.client_secret, code=code,
        )
        return _bind_tokens(db, owner_id=owner_id, config=config, tokens=tokens,
                            started_connection_id=started_id, started_version=started_version,
                            attempt_id=attempt_id)
    except CredentialError as exc:
        db.rollback()
        raise OAuthError(exc.code) from None
    except OAuthError:
        db.rollback()
        raise


def disconnect(db: Session, *, owner_id: int, connection_id: int) -> None:
    connection = db.scalar(select(PlatformConnection).where(
        PlatformConnection.id == connection_id, PlatformConnection.owner_id == owner_id,
        PlatformConnection.platform == "douyin", PlatformConnection.purpose == "publish",
    ).with_for_update().execution_options(populate_existing=True))
    if connection is None:
        raise OAuthError("account_missing")
    secret = db.scalar(select(PublishOAuthSecret).where(
        PublishOAuthSecret.connection_id == connection.id,
    ).with_for_update())
    if secret is not None:
        db.delete(secret)
    connection.status = "revoked"
    connection.credential_ref = None
    connection.credential_version = int(connection.credential_version or 0) + 1
    connection.updated_at = datetime.now(timezone.utc)
    db.commit()


def refresh_connection(db: Session, *, owner_id: int, connection_id: int,
                       client: DouyinOAuthClient | None = None, force: bool = False) -> str:
    """claim/版本/无锁网络/锁后栅栏；返回 refreshed、current、busy 或 stale。"""
    connection = db.scalar(select(PlatformConnection).where(
        PlatformConnection.id == connection_id, PlatformConnection.owner_id == owner_id,
        PlatformConnection.platform == "douyin", PlatformConnection.purpose == "publish",
    ).with_for_update().execution_options(populate_existing=True))
    if connection is None or connection.status != "connected" or not (connection.credential_ref or "").startswith("db:"):
        db.rollback()
        raise OAuthError("account_missing")
    if connection.app_client_key != os.environ.get("DOUYIN_CLIENT_KEY"):
        db.rollback()
        raise OAuthError("wrong_application")
    secret = db.scalar(select(PublishOAuthSecret).where(
        PublishOAuthSecret.connection_id == connection.id,
    ).with_for_update().execution_options(populate_existing=True))
    now = datetime.now(timezone.utc)
    if secret is None or secret.owner_id != owner_id or secret.open_id != connection.external_account_id or secret.client_key != connection.app_client_key:
        db.rollback()
        raise OAuthError("credential_invalid")
    if not force and secret.access_expires_at > now + timedelta(minutes=5):
        db.rollback()
        return "current"
    if secret.refresh_expires_at <= now:
        connection.status = "expired"
        db.commit()
        raise OAuthError("reauthorize_required")
    if secret.refresh_claim_token and secret.refresh_claim_until and secret.refresh_claim_until > now:
        db.rollback()
        return "busy"
    if secret.refresh_claim_token:
        db.rollback()
        raise OAuthError("refresh_unknown")
    claim = secrets.token_hex(16)
    version = secret.credential_version
    account_version = connection.credential_version
    open_id, client_key = secret.open_id, secret.client_key
    try:
        refresh_token = decrypt_token(
            secret.key_version, secret.refresh_ciphertext, owner_id=owner_id, connection_id=connection_id,
            client_key=client_key, open_id=open_id,
        )
    except CredentialError:
        db.rollback()
        raise OAuthError("credential_invalid") from None
    secret.refresh_claim_token = claim
    secret.refresh_claim_until = now + REFRESH_CLAIM
    db.commit()
    try:
        result = (client or DouyinOAuthClient()).refresh(client_key=client_key, refresh_token=refresh_token)
    except OAuthError as exc:
        if exc.code not in {"exchange_unknown", "response_invalid"}:
            _finish_refresh_error(db, connection_id, claim, version, exc.code)
        raise
    connection = db.scalar(select(PlatformConnection).where(
        PlatformConnection.id == connection_id,
    ).with_for_update().execution_options(populate_existing=True))
    secret = db.scalar(select(PublishOAuthSecret).where(
        PublishOAuthSecret.connection_id == connection_id,
    ).with_for_update().execution_options(populate_existing=True))
    if (connection is None or secret is None or connection.owner_id != owner_id or connection.status != "connected"
            or connection.credential_version != account_version or secret.credential_version != version
            or secret.refresh_claim_token != claim or secret.refresh_claim_until <= datetime.now(timezone.utc)
            or connection.external_account_id != open_id
            or result.open_id != open_id or connection.app_client_key != client_key
            or connection.credential_ref != f"db:{secret.id}"):
        db.rollback()
        return "stale"
    stamp = datetime.now(timezone.utc)
    access_key, access_cipher = encrypt_token(
        result.access_token, owner_id=owner_id, connection_id=connection_id, client_key=client_key, open_id=open_id,
    )
    secret.access_ciphertext = access_cipher
    refresh_key, refresh_cipher = encrypt_token(
        result.refresh_token or refresh_token, owner_id=owner_id, connection_id=connection_id,
        client_key=client_key, open_id=open_id,
    )
    if refresh_key != access_key:
        db.rollback()
        raise OAuthError("encryption_unavailable")
    secret.refresh_ciphertext = refresh_cipher
    secret.key_version = access_key
    secret.access_expires_at = stamp + timedelta(seconds=result.expires_in)
    if result.refresh_expires_in is not None:
        secret.refresh_expires_at = min(secret.refresh_expires_at,
                                        stamp + timedelta(seconds=result.refresh_expires_in))
    secret.credential_version = version + 1
    secret.refresh_claim_token = None
    secret.refresh_claim_until = None
    secret.updated_at = stamp
    connection.credential_version = account_version + 1
    connection.expires_at = secret.access_expires_at
    connection.scope_set = list(result.scopes)
    connection.updated_at = stamp
    db.commit()
    return "refreshed"


def _finish_refresh_error(db: Session, connection_id: int, claim: str, version: int, code: str) -> None:
    connection = db.scalar(select(PlatformConnection).where(
        PlatformConnection.id == connection_id,
    ).with_for_update().execution_options(populate_existing=True))
    secret = db.scalar(select(PublishOAuthSecret).where(
        PublishOAuthSecret.connection_id == connection_id,
    ).with_for_update().execution_options(populate_existing=True))
    if secret is None or secret.refresh_claim_token != claim or secret.credential_version != version:
        db.rollback()
        return
    secret.refresh_claim_token = None
    secret.refresh_claim_until = None
    if code in {"authorization_rejected", "permission_denied", "auth_expired", "reauthorize_required"}:
        if connection is not None and connection.credential_version == version:
            connection.status = "expired"
            connection.updated_at = datetime.now(timezone.utc)
    db.commit()
