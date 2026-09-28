"""Redis 只做登录 Session，不做任务队列。"""

import redis

from app.core.config import settings

redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASSWORD or None,
    db=0,
    decode_responses=True,
)


def session_key(token: str) -> str:
    return f"session:{token}"
