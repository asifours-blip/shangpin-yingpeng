"""演示社交样例：读 JSON、查找账号、幂等同步联系人。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SocialAccount, SocialContact

# 相对本文件定位到应用包内 data/，不写死盘符与用户名
_SAMPLES_PATH = Path(__file__).resolve().parent.parent / "data" / "social_samples.json"


@lru_cache(maxsize=1)
def load_samples() -> tuple[dict[str, Any], ...]:
    raw = json.loads(_SAMPLES_PATH.read_text(encoding="utf-8"))
    accounts = raw.get("accounts") if isinstance(raw, dict) else raw
    if not isinstance(accounts, list):
        return ()
    return tuple(item for item in accounts if isinstance(item, dict))


def find_sample(platform: str, external_account_id: str) -> dict[str, Any] | None:
    for item in load_samples():
        if item.get("platform") == platform and item.get("external_account_id") == external_account_id:
            return item
    return None


def sync_contacts(db: Session, account: SocialAccount) -> int:
    """按样例 upsert 联系人。不覆盖已有 remark/tags；更新 last_synced_at。幂等。"""
    sample = find_sample(account.platform, account.external_account_id)
    if sample is None:
        return 0

    contacts = sample.get("contacts") or []
    synced = 0
    for item in contacts:
        if not isinstance(item, dict):
            continue
        ext_id = item.get("external_user_id")
        nickname = item.get("nickname")
        if not ext_id or not nickname:
            continue

        row = db.scalar(
            select(SocialContact).where(
                SocialContact.account_id == account.id,
                SocialContact.external_user_id == ext_id,
            )
        )
        data_source = item.get("data_source") or "sample"
        avatar_url = item.get("avatar_url")
        if row is None:
            db.add(
                SocialContact(
                    account_id=account.id,
                    external_user_id=ext_id,
                    nickname=nickname,
                    avatar_url=avatar_url,
                    remark=item.get("remark"),
                    tags=list(item.get("tags") or []),
                    data_source=data_source,
                )
            )
        else:
            row.nickname = nickname
            row.avatar_url = avatar_url
            row.data_source = data_source
            # 已有备注 / 标签由用户维护，同步不覆盖
        synced += 1

    account.last_synced_at = datetime.now(timezone.utc)
    db.flush()
    return synced
