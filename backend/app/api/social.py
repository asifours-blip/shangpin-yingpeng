"""演示社交账号与联系人。样例接入，不接真实平台。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import require_unfrozen
from app.core.db import get_db
from app.models import SocialAccount, SocialContact, User
from app.schemas.social import (
    SocialAccountConnectIn,
    SocialAccountListOut,
    SocialAccountOut,
    SocialAccountStatusIn,
    SocialContactAggListOut,
    SocialContactAggOut,
    SocialContactListOut,
    SocialContactOut,
    SocialContactPatchIn,
    SocialSyncOut,
)
from app.services.social_samples import find_sample, sync_contacts

router = APIRouter(prefix="/api/social", tags=["social"])

_ALLOWED_STATUS = {"connected", "disconnected"}


def _account_or_404(db: Session, account_id: int, user: User) -> SocialAccount:
    account = db.get(SocialAccount, account_id)
    if account is None or account.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    return account


def _contact_or_404(db: Session, contact_id: int, user: User) -> SocialContact:
    contact = db.scalar(
        select(SocialContact)
        .join(SocialAccount, SocialContact.account_id == SocialAccount.id)
        .options(joinedload(SocialContact.account))
        .where(SocialContact.id == contact_id, SocialAccount.owner_id == user.id)
    )
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="联系人不存在")
    return contact


def _agg_out(contact: SocialContact) -> SocialContactAggOut:
    acc = contact.account
    return SocialContactAggOut(
        id=contact.id,
        account_id=contact.account_id,
        platform=acc.platform,
        display_name=acc.display_name,
        external_account_id=acc.external_account_id,
        external_user_id=contact.external_user_id,
        nickname=contact.nickname,
        avatar_url=contact.avatar_url,
        remark=contact.remark,
        tags=list(contact.tags or []),
        data_source=contact.data_source,
    )


@router.get("/accounts", response_model=SocialAccountListOut)
def list_accounts(
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> SocialAccountListOut:
    rows = db.scalars(
        select(SocialAccount)
        .where(SocialAccount.owner_id == user.id)
        .order_by(SocialAccount.id.asc())
    ).all()
    return SocialAccountListOut(items=[SocialAccountOut.model_validate(row) for row in rows])


@router.post("/accounts", response_model=SocialAccountOut)
def connect_account(
    body: SocialAccountConnectIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> SocialAccount:
    platform = body.platform.strip()
    external_account_id = body.external_account_id.strip()
    sample = find_sample(platform, external_account_id)
    if sample is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持演示样例账号，请检查 platform 与 external_account_id",
        )

    display_name = str(sample.get("display_name") or external_account_id)
    data_source = str(sample.get("data_source") or "sample")

    account = db.scalar(
        select(SocialAccount).where(
            SocialAccount.owner_id == user.id,
            SocialAccount.platform == platform,
            SocialAccount.external_account_id == external_account_id,
        )
    )
    if account is None:
        account = SocialAccount(
            owner_id=user.id,
            platform=platform,
            external_account_id=external_account_id,
            display_name=display_name,
            status="connected",
            data_source=data_source,
        )
        db.add(account)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            account = db.scalar(
                select(SocialAccount).where(
                    SocialAccount.owner_id == user.id,
                    SocialAccount.platform == platform,
                    SocialAccount.external_account_id == external_account_id,
                )
            )
            if account is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="连接失败，请重试",
                ) from None

    account.status = "connected"
    account.display_name = display_name
    account.data_source = data_source
    sync_contacts(db, account)
    db.commit()
    db.refresh(account)
    return account


@router.patch("/accounts/{account_id}", response_model=SocialAccountOut)
def patch_account(
    account_id: int,
    body: SocialAccountStatusIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> SocialAccount:
    if body.status not in _ALLOWED_STATUS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status 只能是 connected 或 disconnected",
        )
    account = _account_or_404(db, account_id, user)
    account.status = body.status
    db.commit()
    db.refresh(account)
    return account


@router.post("/accounts/{account_id}/sync", response_model=SocialSyncOut)
def sync_account(
    account_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> SocialSyncOut:
    account = _account_or_404(db, account_id, user)
    synced = sync_contacts(db, account)
    db.commit()
    return SocialSyncOut(synced=synced)


@router.get("/accounts/{account_id}/contacts", response_model=SocialContactListOut)
def list_account_contacts(
    account_id: int,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> SocialContactListOut:
    _account_or_404(db, account_id, user)
    rows = db.scalars(
        select(SocialContact)
        .where(SocialContact.account_id == account_id)
        .order_by(SocialContact.id.asc())
    ).all()
    return SocialContactListOut(items=[SocialContactOut.model_validate(row) for row in rows])


@router.get("/contacts", response_model=SocialContactAggListOut)
def list_contacts(
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
    platform: str | None = Query(None),
    account_id: int | None = Query(None),
    q: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> SocialContactAggListOut:
    stmt = (
        select(SocialContact)
        .join(SocialAccount, SocialContact.account_id == SocialAccount.id)
        .options(joinedload(SocialContact.account))
        .where(
            SocialAccount.owner_id == user.id,
            SocialAccount.status == "connected",
        )
    )
    platform_value = (platform or "").strip()
    if platform_value:
        stmt = stmt.where(SocialAccount.platform == platform_value)
    if account_id is not None:
        stmt = stmt.where(SocialAccount.id == account_id)
    keyword = (q or "").strip()
    if keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                SocialContact.nickname.ilike(pattern),
                SocialContact.external_user_id.ilike(pattern),
            )
        )
    rows = db.scalars(
        stmt.order_by(SocialContact.id.asc()).offset(skip).limit(limit)
    ).unique().all()
    return SocialContactAggListOut(items=[_agg_out(row) for row in rows])


@router.patch("/contacts/{contact_id}", response_model=SocialContactOut)
def patch_contact(
    contact_id: int,
    body: SocialContactPatchIn,
    user: User = Depends(require_unfrozen),
    db: Session = Depends(get_db),
) -> SocialContact:
    contact = _contact_or_404(db, contact_id, user)
    if "remark" in body.model_fields_set:
        contact.remark = body.remark
    if "tags" in body.model_fields_set:
        contact.tags = list(body.tags or [])
    db.commit()
    db.refresh(contact)
    return contact
