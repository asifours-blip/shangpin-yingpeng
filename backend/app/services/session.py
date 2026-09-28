"""登录 Session 服务。"""

import secrets

from app.core.config import settings
from app.core.redis_client import redis_client, session_key


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    redis_client.setex(session_key(token), settings.SESSION_TTL_SECONDS, str(user_id))
    return token


def get_session_user_id(token: str) -> int | None:
    raw = redis_client.get(session_key(token))
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def destroy_session(token: str) -> None:
    redis_client.delete(session_key(token))
