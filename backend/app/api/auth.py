"""登录 / 退出 / 当前用户。Session 进 Redis。"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.db import get_db
from app.core.security import verify_password
from app.models import FreezeEvent, User
from app.schemas import LoginIn, UserOut
from app.services.session import create_session, destroy_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.SESSION_TTL_SECONDS,
        path="/",
    )


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)) -> User:
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")
    token = create_session(user.id)
    _set_cookie(response, token)
    return user


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    token = request.cookies.get(settings.SESSION_COOKIE)
    if token:
        destroy_session(token)
    response.delete_cookie(settings.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    # User 没有 freeze_reason / frozen_at；解冻后即使 current_freeze_event_id 仍在也不展示
    freeze_reason = None
    frozen_at = None
    if user.is_frozen and user.current_freeze_event_id:
        event = db.get(FreezeEvent, user.current_freeze_event_id)
        if event is not None:
            freeze_reason = event.reason
            frozen_at = event.frozen_at
    return UserOut(
        id=user.id,
        username=user.username,
        role=user.role,
        is_frozen=user.is_frozen,
        freeze_reason=freeze_reason,
        frozen_at=frozen_at,
    )
