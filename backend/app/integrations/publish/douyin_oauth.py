"""网站应用 OAuth HTTP 契约；响应只转成安全字段。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

SCOPE = "video.create.bind"
AUTHORIZE_URL = "https://open.douyin.com/platform/oauth/connect/"
TOKEN_URL = "https://open.douyin.com/oauth/access_token/"
REFRESH_URL = "https://open.douyin.com/oauth/refresh_token/"


class OAuthError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class OAuthTokens:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    open_id: str
    scopes: tuple[str, ...]
    expires_in: int
    refresh_expires_in: int | None


def _seconds(value: object) -> int:
    if isinstance(value, bool) or not (isinstance(value, int) or (isinstance(value, str) and value.isdecimal())):
        raise OAuthError("response_invalid")
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise OAuthError("response_invalid") from exc
    if seconds <= 0 or seconds > 10 * 365 * 86400:
        raise OAuthError("response_invalid")
    return seconds


def _parse(response: httpx.Response, *, initial: bool, sensitive: tuple[str, ...]) -> OAuthTokens:
    if response.status_code >= 500 or response.status_code == 429:
        raise OAuthError("exchange_unknown")
    if response.status_code != 200:
        raise OAuthError("authorization_rejected")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthError("response_invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise OAuthError("response_invalid")
    data = payload["data"]
    try:
        code = int(data.get("error_code"))
    except (TypeError, ValueError) as exc:
        raise OAuthError("response_invalid") from exc
    if code != 0:
        if code == 10004:
            raise OAuthError("permission_denied")
        if code == 10007:
            raise OAuthError("authorization_rejected")
        if code == 10008:
            raise OAuthError("auth_expired")
        if code == 10010:
            raise OAuthError("reauthorize_required")
        if code == 10013:
            raise OAuthError("oauth_unconfigured")
        if code == 10014:
            raise OAuthError("wrong_application")
        raise OAuthError("exchange_unknown")
    access, open_id, refresh = data.get("access_token"), data.get("open_id"), data.get("refresh_token")
    scope = data.get("scope")
    if not isinstance(access, str) or not access or not isinstance(open_id, str) or not 0 < len(open_id) <= 128:
        raise OAuthError("response_invalid")
    if initial and (not isinstance(refresh, str) or not refresh):
        raise OAuthError("response_invalid")
    if refresh is not None and (not isinstance(refresh, str) or not refresh):
        raise OAuthError("response_invalid")
    if not isinstance(scope, str):
        raise OAuthError("response_invalid")
    scopes = tuple(item.strip() for item in scope.split(",") if item.strip())
    forbidden = tuple(value for value in (access, refresh, *sensitive) if isinstance(value, str) and value)
    if (len(scopes) > 50 or any(len(item) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", item) for item in scopes)
            or any(token in open_id or token in scope for token in forbidden)
            or any(ord(char) < 32 for char in open_id)):
        raise OAuthError("response_invalid")
    if SCOPE not in scopes:
        raise OAuthError("permission_denied")
    return OAuthTokens(
        access_token=access, refresh_token=refresh, open_id=open_id, scopes=scopes,
        expires_in=_seconds(data.get("expires_in")),
        refresh_expires_in=_seconds(data.get("refresh_expires_in")) if initial or data.get("refresh_expires_in") is not None else None,
    )


class DouyinOAuthClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport
        self.isolated = isinstance(transport, httpx.MockTransport)

    def authorization_url(self, *, client_key: str, callback_uri: str, state: str) -> str:
        return AUTHORIZE_URL + "?" + urlencode({
            "client_key": client_key, "response_type": "code", "scope": SCOPE,
            "redirect_uri": callback_uri, "state": state,
        })

    def _request(self, url: str, fields: dict[str, str]) -> httpx.Response:
        if os.environ.get("DOUYIN_OAUTH_ENABLED") != "1" and not self.isolated:
            raise OAuthError("oauth_disabled")
        try:
            with httpx.Client(transport=self.transport, timeout=10, follow_redirects=False) as client:
                return client.post(url, data=fields, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise OAuthError("exchange_unknown") from None

    def exchange(self, *, client_key: str, client_secret: str, code: str) -> OAuthTokens:
        return _parse(self._request(TOKEN_URL, {
            "client_key": client_key, "client_secret": client_secret,
            "code": code, "grant_type": "authorization_code",
        }), initial=True, sensitive=(code, client_secret))

    def refresh(self, *, client_key: str, refresh_token: str) -> OAuthTokens:
        return _parse(self._request(REFRESH_URL, {
            "client_key": client_key, "grant_type": "refresh_token", "refresh_token": refresh_token,
        }), initial=False, sensitive=(refresh_token,))
