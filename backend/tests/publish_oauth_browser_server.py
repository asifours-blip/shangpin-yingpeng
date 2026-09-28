"""仅隔离验收启动：真实 API/PG/Redis，OAuth HTTP 用内存 transport。"""

from __future__ import annotations

import os
from urllib.parse import parse_qs

import httpx
import uvicorn
from sqlalchemy import delete, select

from app.api.publish_accounts import get_oauth_client
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.integrations.publish.douyin_oauth import DouyinOAuthClient
from app.main import app
from app.models import PlatformConnection, PublishOAuthSecret, PublishOAuthState, User


def synthetic_oauth(request: httpx.Request) -> httpx.Response:
    form = parse_qs(request.content.decode())
    assert request.url.host == "open.douyin.com"
    assert form.get("client_key") == ["isolated-browser-app"]
    if request.url.path.endswith("/access_token/"):
        assert form.get("code") == ["browser-code"]
        assert form.get("client_secret") == ["synthetic-browser-client-secret"]
        assert form.get("grant_type") == ["authorization_code"]
    elif request.url.path.endswith("/refresh_token/"):
        assert form.get("grant_type") == ["refresh_token"]
    else:
        raise AssertionError("unexpected OAuth endpoint")
    return httpx.Response(200, json={"data": {
        "error_code": 0, "access_token": "synthetic-browser-access", "refresh_token": "synthetic-browser-refresh",
        "open_id": "browser-open-id", "scope": "video.create.bind", "expires_in": 3600,
        "refresh_expires_in": 7200,
    }, "extra": {"error_code": 0}})


def main() -> None:
    if (os.environ.get("POSTGRES_DB") != "codex_stage5c_20260926"
            or os.environ.get("APP_ENV") != "test" or os.environ.get("PUBLISH_LIVE") != "0"):
        raise RuntimeError("browser stub only runs against the named isolated database with live publish off")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "ops_oauth_browser_5c"))
        if user is None:
            user = User(username="ops_oauth_browser_5c", password_hash=hash_password("synthetic-browser-password"),
                        role="user", is_active=True, is_frozen=False)
            db.add(user)
            db.flush()
        # 仅重置隔离库中本脚本专用用户的 OAuth 夹具，便于原始浏览器验收复跑。
        db.execute(delete(PublishOAuthState).where(PublishOAuthState.owner_id == user.id))
        db.execute(delete(PublishOAuthSecret).where(PublishOAuthSecret.owner_id == user.id))
        db.execute(delete(PlatformConnection).where(
            PlatformConnection.owner_id == user.id, PlatformConnection.platform == "douyin",
            PlatformConnection.purpose == "publish",
        ))
        db.commit()
    app.dependency_overrides[get_oauth_client] = lambda: DouyinOAuthClient(transport=httpx.MockTransport(synthetic_oauth))
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=True)


if __name__ == "__main__":
    main()
