"""部署主密钥管理；库中只保存 AES-GCM nonce + ciphertext。"""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from app.integrations.publish.credentials import CredentialError


def _keys() -> dict[str, bytes]:
    try:
        raw = json.loads(os.environ.get("DOUYIN_OAUTH_KEYS", ""))
        if not isinstance(raw, dict):
            raise ValueError
        keys = {version: base64.urlsafe_b64decode(encoded) for version, encoded in raw.items()
                if isinstance(version, str) and isinstance(encoded, str)}
        if len(keys) != len(raw) or any(len(key) != 32 for key in keys.values()):
            raise ValueError
        return keys
    except (ValueError, TypeError, binascii.Error) as exc:
        raise CredentialError("encryption_unavailable") from exc


def active_version() -> str:
    version = os.environ.get("DOUYIN_OAUTH_ACTIVE_KEY_VERSION", "")
    if not version or version not in _keys():
        raise CredentialError("encryption_unavailable")
    return version


def _aad(owner_id: int, connection_id: int, client_key: str, open_id: str, purpose: str) -> bytes:
    return json.dumps([owner_id, connection_id, client_key, open_id, purpose], separators=(",", ":")).encode()


def encrypt_token(value: str, *, owner_id: int, connection_id: int, client_key: str,
                  open_id: str, purpose: str = "publish") -> tuple[str, bytes]:
    if not isinstance(value, str) or not value:
        raise CredentialError("credential_invalid")
    version = active_version()
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_keys()[version]).encrypt(
        nonce, value.encode(), _aad(owner_id, connection_id, client_key, open_id, purpose),
    )
    return version, nonce + ciphertext


def decrypt_token(version: str, blob: bytes, *, owner_id: int, connection_id: int,
                  client_key: str, open_id: str, purpose: str = "publish") -> str:
    try:
        key = _keys()[version]
        if len(blob) < 29:
            raise ValueError
        return AESGCM(key).decrypt(
            blob[:12], blob[12:], _aad(owner_id, connection_id, client_key, open_id, purpose),
        ).decode()
    except (KeyError, ValueError, UnicodeDecodeError, TypeError, InvalidTag) as exc:
        raise CredentialError("credential_invalid") from exc
