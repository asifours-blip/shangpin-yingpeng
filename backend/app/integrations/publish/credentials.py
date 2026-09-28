"""发布凭证部署映射。业务库只保存 env: 引用，不保存 token。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


_ENV_REF = re.compile(r"env:([A-Z][A-Z0-9_]{0,127})\Z")
_DB_REF = re.compile(r"db:([1-9][0-9]*)\Z")
_REQUIRED_SCOPE = "video.create.bind"


class CredentialError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class PublishCredential:
    access_token: str = field(repr=False)
    open_id: str
    client_key: str
    credential_version: int = 0


def _expiry(value: Any) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CredentialError("credential_invalid") from exc
    else:
        raise CredentialError("credential_invalid")
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


def _scope_values(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CredentialError("credential_invalid")
    return value


def resolve_credential(connection, *, owner_id: int, target_open_id: str) -> PublishCredential:
    """核对数据库连接、冻结目标和部署 secret 的所有绑定，不输出原始值。"""
    if connection.owner_id != owner_id:
        raise CredentialError("wrong_owner")
    if connection.platform != "douyin" or connection.purpose != "publish" or connection.status != "connected":
        raise CredentialError("wrong_connection")
    if not target_open_id or connection.external_account_id != target_open_id:
        raise CredentialError("wrong_account")
    if _REQUIRED_SCOPE not in _scope_values(connection.scope_set):
        raise CredentialError("wrong_scope")
    if connection.expires_at is None or _expiry(connection.expires_at) <= datetime.now(timezone.utc):
        raise CredentialError("expired")
    database_ref = _DB_REF.fullmatch(connection.credential_ref or "")
    if database_ref is not None:
        from app.core.db import SessionLocal
        from app.integrations.publish.oauth_crypto import decrypt_token
        from app.models.publish_oauth import PublishOAuthSecret

        with SessionLocal() as db:
            secret = db.get(PublishOAuthSecret, int(database_ref.group(1)))
            if secret is None or secret.connection_id != connection.id or secret.owner_id != owner_id:
                raise CredentialError("wrong_connection")
            if secret.open_id != target_open_id:
                raise CredentialError("wrong_account")
            client_key = os.environ.get("DOUYIN_CLIENT_KEY")
            if not client_key or secret.client_key != client_key or connection.app_client_key != client_key:
                raise CredentialError("wrong_application")
            if secret.credential_version != connection.credential_version:
                raise CredentialError("credential_stale")
            if _expiry(secret.access_expires_at) <= datetime.now(timezone.utc):
                raise CredentialError("expired")
            token = decrypt_token(secret.key_version, secret.access_ciphertext, owner_id=owner_id,
                                  connection_id=connection.id, client_key=client_key, open_id=target_open_id)
            return PublishCredential(token, target_open_id, client_key, secret.credential_version)
    matched = _ENV_REF.fullmatch(connection.credential_ref or "")
    if matched is None:
        raise CredentialError("credential_missing")
    raw = os.environ.get(matched.group(1))
    if not raw:
        raise CredentialError("credential_missing")
    try:
        secret = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise CredentialError("credential_invalid") from exc
    if not isinstance(secret, dict):
        raise CredentialError("credential_invalid")
    if secret.get("owner_id") != owner_id:
        raise CredentialError("wrong_owner")
    if secret.get("connection_id") != connection.id or secret.get("platform") != "douyin" or secret.get("purpose") != "publish":
        raise CredentialError("wrong_connection")
    if secret.get("open_id") != target_open_id:
        raise CredentialError("wrong_account")
    if _REQUIRED_SCOPE not in _scope_values(secret.get("scopes")):
        raise CredentialError("wrong_scope")
    if _expiry(secret.get("expires_at")) <= datetime.now(timezone.utc):
        raise CredentialError("expired")
    client_key = os.environ.get("DOUYIN_CLIENT_KEY")
    if not client_key or secret.get("client_key") != client_key:
        raise CredentialError("wrong_application")
    token = secret.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise CredentialError("credential_invalid")
    return PublishCredential(access_token=token, open_id=target_open_id, client_key=client_key)
