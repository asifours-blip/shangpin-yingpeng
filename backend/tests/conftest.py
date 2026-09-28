"""专项测试公共夹具。隔离用户名，不碰 demo/admin/任务 5，不调方舟。"""

from __future__ import annotations

import uuid
import os
from pathlib import Path
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app.core.db import SessionLocal, engine


@pytest.fixture(autouse=True)
def local_test_font(monkeypatch):
    """Windows 测试使用系统中文字体；生产渲染配置保持不变。"""
    if os.name == "nt":
        from app.services import media_render

        if not media_render.FONT_PATH.exists():
            monkeypatch.setattr(media_render, "FONT_PATH", Path(os.environ["WINDIR"]) / "Fonts" / "msyh.ttc")


def pytest_configure(config) -> None:
    # 现有工程 Settings 从 .env 读演示库名；本轮测试仍连同一 PG 实例，
    # 但只用 ops_ 前缀用户。禁止测 demo/admin，禁止改任务 5。
    config.addinivalue_line(
        "markers",
        "real_storage: 使用当前配置的对象存储，不用内存替身",
    )


def pg_available() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture()
def db() -> Generator:
    if not pg_available():
        pytest.skip("PostgreSQL unavailable")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    if not pg_available():
        pytest.skip("PostgreSQL unavailable")
    from app.main import app
    from app.api.deps import get_current_user

    with (
        patch("app.main.ensure_bucket"),
        patch("app.main.seed_users_if_empty"),
    ):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.clear()


def suffix() -> str:
    return uuid.uuid4().hex[:10]


def make_user(db, *, role: str = "user", frozen: bool = False, prefix: str = "ops"):
    from app.core.security import hash_password
    from app.models import User

    user = User(
        username=f"{prefix}_{role}_{suffix()}",
        password_hash=hash_password("test-pass-not-used"),
        role=role,
        is_active=True,
        is_frozen=frozen,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_asset(db, owner, *, key: str | None = None):
    from app.models import ImageAsset

    asset = ImageAsset(
        owner_id=owner.id,
        bucket="aigc-images",
        object_key=key or f"tests/ops/{suffix()}.png",
        mime="image/png",
        size_bytes=12,
        width=64,
        height=64,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def as_user(user) -> None:
    from app.api.deps import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user


def cleanup(db, *users) -> None:
    """按外键顺序清本测试用户。绝不按 username 模糊删 demo。"""
    from app.models import (
        Appeal,
        CopywritingOperation,
        FreezeEvent,
        GenerationTask,
        GenerationTaskAsset,
        ImageAsset,
        Notification,
        PromptOperation,
        SocialAccount,
        SocialContact,
        User,
    )
    from app.models.campaign import Campaign, CampaignRun, ContentVariant, PipelineStep, VariantAsset, VariantReview
    from app.models.operation_plan import OperationPlan, OperationPlanRun, OperationPlanDailyUsage, OperationPlanCampaign
    from app.models.product import OwnedProduct, ProductFactVersion
    from app.models.publish import PublishJob
    from app.models.source import CollectionConfig, CollectionRun, PlatformConnection, SourceItem
    from app.models.publish_oauth import PublishOAuthSecret, PublishOAuthState

    ids = [u.id for u in users if u is not None]
    if not ids:
        return
    plan_ids = list(db.scalars(select(OperationPlan.id).where(OperationPlan.owner_id.in_(ids))).all())
    if plan_ids:
        plan_run_ids = list(db.scalars(select(OperationPlanRun.id).where(OperationPlanRun.plan_id.in_(plan_ids))).all())
        if plan_run_ids:
            db.execute(delete(OperationPlanCampaign).where(OperationPlanCampaign.plan_run_id.in_(plan_run_ids)))
            db.execute(delete(OperationPlanRun).where(OperationPlanRun.id.in_(plan_run_ids)))
        db.execute(delete(OperationPlanDailyUsage).where(OperationPlanDailyUsage.plan_id.in_(plan_ids)))
        db.execute(delete(OperationPlan).where(OperationPlan.id.in_(plan_ids)))
    db.execute(delete(PublishJob).where(PublishJob.owner_id.in_(ids)))
    campaign_ids = list(db.scalars(select(Campaign.id).where(Campaign.owner_id.in_(ids))).all())
    if campaign_ids:
        variant_ids = list(
            db.scalars(select(ContentVariant.id).where(ContentVariant.campaign_id.in_(campaign_ids))).all()
        )
        run_ids = list(db.scalars(select(CampaignRun.id).where(CampaignRun.campaign_id.in_(campaign_ids))).all())
        if variant_ids:
            db.execute(delete(VariantReview).where(VariantReview.variant_id.in_(variant_ids)))
            db.execute(delete(VariantAsset).where(VariantAsset.variant_id.in_(variant_ids)))
        if run_ids:
            db.execute(delete(PipelineStep).where(PipelineStep.run_id.in_(run_ids)))
        db.execute(delete(ContentVariant).where(ContentVariant.campaign_id.in_(campaign_ids)))
        db.execute(delete(CampaignRun).where(CampaignRun.campaign_id.in_(campaign_ids)))
        db.execute(delete(Campaign).where(Campaign.id.in_(campaign_ids)))
    config_ids = list(db.scalars(select(CollectionConfig.id).where(CollectionConfig.owner_id.in_(ids))).all())
    if config_ids:
        source_run_ids = list(
            db.scalars(select(CollectionRun.id).where(CollectionRun.config_id.in_(config_ids))).all()
        )
        if source_run_ids:
            db.execute(delete(SourceItem).where(SourceItem.run_id.in_(source_run_ids)))
            db.execute(delete(CollectionRun).where(CollectionRun.id.in_(source_run_ids)))
        db.execute(delete(CollectionConfig).where(CollectionConfig.id.in_(config_ids)))
    db.execute(delete(PublishOAuthState).where(PublishOAuthState.owner_id.in_(ids)))
    db.execute(delete(PublishOAuthSecret).where(PublishOAuthSecret.owner_id.in_(ids)))
    db.execute(delete(PlatformConnection).where(PlatformConnection.owner_id.in_(ids)))
    product_ids = list(db.scalars(select(OwnedProduct.id).where(OwnedProduct.owner_id.in_(ids))).all())
    if product_ids:
        db.execute(delete(ProductFactVersion).where(ProductFactVersion.product_id.in_(product_ids)))
        db.execute(delete(OwnedProduct).where(OwnedProduct.id.in_(product_ids)))

    ids = [u.id for u in users if u is not None]
    if not ids:
        return
    task_ids = list(db.scalars(select(GenerationTask.id).where(GenerationTask.user_id.in_(ids))).all())
    account_ids = list(db.scalars(select(SocialAccount.id).where(SocialAccount.owner_id.in_(ids))).all())
    if task_ids:
        db.execute(delete(Notification).where(Notification.task_id.in_(task_ids)))
        db.execute(delete(GenerationTaskAsset).where(GenerationTaskAsset.task_id.in_(task_ids)))
        db.execute(delete(GenerationTask).where(GenerationTask.id.in_(task_ids)))
    db.execute(delete(Notification).where(Notification.user_id.in_(ids)))
    db.execute(delete(CopywritingOperation).where(CopywritingOperation.user_id.in_(ids)))
    if account_ids:
        db.execute(delete(SocialContact).where(SocialContact.account_id.in_(account_ids)))
        db.execute(delete(SocialAccount).where(SocialAccount.id.in_(account_ids)))
    db.execute(delete(Appeal).where(Appeal.user_id.in_(ids)))
    db.execute(delete(PromptOperation).where(PromptOperation.user_id.in_(ids)))
    for u in db.scalars(select(User).where(User.id.in_(ids))).all():
        u.current_freeze_event_id = None
    db.flush()
    db.execute(delete(FreezeEvent).where(FreezeEvent.user_id.in_(ids)))
    db.execute(delete(ImageAsset).where(ImageAsset.owner_id.in_(ids)))
    db.execute(delete(User).where(User.id.in_(ids)))
    db.commit()
