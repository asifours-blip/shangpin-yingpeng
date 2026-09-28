"""真实发布账号管理；演示联系人继续由 /api/social 单独提供。"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_unfrozen
from app.core.config import settings
from app.core.db import get_db
from app.integrations.publish.douyin import DouyinPublishAdapter
from app.integrations.publish.douyin_oauth import DouyinOAuthClient, OAuthError
from app.models import PlatformConnection, User
from app.services.publish_oauth import (
    configuration, configuration_missing, disconnect, finish_authorization,
    refresh_connection, start_authorization,
)

router = APIRouter(prefix="/api/publish-accounts", tags=["publish-accounts"])


class _RedactOAuthCallback(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            callback = "/api/publish-accounts/douyin/callback"
            if path.startswith(callback + "?"):
                args = list(args)
                args[2] = callback
                record.args = tuple(args)
        return True


_access_logger = logging.getLogger("uvicorn.access")
if not any(isinstance(item, _RedactOAuthCallback) for item in _access_logger.filters):
    _access_logger.addFilter(_RedactOAuthCallback())


def get_oauth_client() -> DouyinOAuthClient:
    return DouyinOAuthClient()


class StartIn(BaseModel):
    connection_id: int | None = None


def _guard(exc: OAuthError) -> None:
    code = exc.code
    status = 409 if code in {"authorization_stale", "account_missing", "reauthorize_required"} else 400
    if code in {"oauth_disabled", "oauth_unconfigured"}:
        status = 503
    raise HTTPException(status_code=status, detail={"code": code, "message": "授权未完成，请检查配置或重新开始"},
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}) from None


def _public(connection: PlatformConnection) -> dict:
    missing = DouyinPublishAdapter().check_capability(connection)
    return {
        "id": connection.id, "platform": connection.platform, "purpose": connection.purpose,
        "external_account_id": connection.external_account_id, "status": connection.status,
        "scopes": list(connection.scope_set or []), "expires_at": connection.expires_at,
        "credential_kind": "managed" if connection.credential_origin == "managed" else "deployment",
        "missing": missing, "app_publish_capability": "pending_verification",
    }


@router.get("")
def list_publish_accounts(user: User = Depends(require_unfrozen), db: Session = Depends(get_db)) -> dict:
    connections = db.scalars(select(PlatformConnection).where(
        PlatformConnection.owner_id == user.id, PlatformConnection.purpose == "publish",
    ).order_by(PlatformConnection.id)).all()
    return {
        "items": [_public(item) for item in connections],
        "douyin": {"oauth_enabled": os.environ.get("DOUYIN_OAUTH_ENABLED") == "1",
                   "configuration_missing": configuration_missing(),
                   "app_publish_capability": "pending_verification"},
        "xiaohongshu": {"implemented": False, "status": "pending_integration"},
    }


@router.post("/douyin/start")
def start_douyin(request: Request, body: StartIn | None = None, user: User = Depends(require_unfrozen),
                 db: Session = Depends(get_db), client: DouyinOAuthClient = Depends(get_oauth_client)) -> dict:
    try:
        url = start_authorization(db, owner_id=user.id,
                                  session_cookie=request.cookies.get(settings.SESSION_COOKIE),
                                  connection_id=body.connection_id if body else None, client=client)
        return {"authorization_url": url}
    except OAuthError as exc:
        _guard(exc)


@router.get("/douyin/callback")
def callback(request: Request, user: User = Depends(require_unfrozen), db: Session = Depends(get_db),
             client: DouyinOAuthClient = Depends(get_oauth_client)):
    # 不声明 code/state 为 query 参数：验证错误绝不回显原始 URL 或值。
    state, code = request.query_params.get("state") or "", request.query_params.get("code")
    try:
        frontend_origin = configuration().frontend_origin
    except OAuthError as exc:
        _guard(exc)
    try:
        finish_authorization(db, owner_id=user.id, session_cookie=request.cookies.get(settings.SESSION_COOKIE),
                             state=state, code=code, client=client)
        outcome = "connected"
    except OAuthError as exc:
        if exc.code in {"state_invalid", "session_required"}:
            _guard(exc)
        outcome = exc.code
    target = frontend_origin + "/publish-accounts?result=" + outcome
    return RedirectResponse(target, status_code=303,
                            headers={"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})


@router.post("/{connection_id}/refresh")
def refresh_douyin(connection_id: int, user: User = Depends(require_unfrozen),
                   db: Session = Depends(get_db), client: DouyinOAuthClient = Depends(get_oauth_client)) -> dict:
    try:
        result = refresh_connection(db, owner_id=user.id, connection_id=connection_id, client=client, force=True)
        return {"status": result}
    except OAuthError as exc:
        _guard(exc)


@router.post("/{connection_id}/disconnect")
def disconnect_douyin(connection_id: int, user: User = Depends(require_unfrozen),
                      db: Session = Depends(get_db)) -> dict:
    try:
        disconnect(db, owner_id=user.id, connection_id=connection_id)
        return {"status": "disconnected", "platform_revoked": False}
    except OAuthError as exc:
        _guard(exc)
