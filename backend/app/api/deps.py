"""当前登录用户依赖。"""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models import User
from app.services.session import get_session_user_id


ACCOUNT_FROZEN_DETAIL = {"code": "ACCOUNT_FROZEN", "message": "账号已冻结"}


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(settings.SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    user_id = get_session_user_id(token)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期")
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    return user


def require_unfrozen(user: User = Depends(get_current_user)) -> User:
    """业务接口闸门：已登录且未禁用，但冻结则 403。登录/me/申诉不要用这个。"""
    if user.is_frozen:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ACCOUNT_FROZEN_DETAIL,
        )
    return user
