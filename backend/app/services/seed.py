"""启动时若库中无用户，写入 admin / demo。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import User

SEED_USERS = (
    ("admin", "admin123", "admin"),
    ("demo", "demo123", "user"),
)


def seed_users_if_empty(db: Session) -> None:
    exists = db.scalar(select(User.id).limit(1))
    if exists is not None:
        return
    for username, password, role in SEED_USERS:
        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                is_active=True,
            )
        )
    db.commit()
