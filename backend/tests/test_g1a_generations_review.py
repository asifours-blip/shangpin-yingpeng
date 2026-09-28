"""G1-A：生成审核 / product-scene / 来源 op / legacy_unlabeled。

隔离用户名，不碰 demo 的任务 5。PostgreSQL 不可用则整模块 skip。
不启动真实服务、不调方舟；鉴权用 dependency_overrides，避开 Redis/MinIO。
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

# 先探测 PG，再决定是否导入会连库的应用
def _pg_available() -> bool:
    try:
        from app.core.db import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="PostgreSQL unavailable")


from app.api.deps import get_current_user  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    GenerationTask,
    GenerationTaskAsset,
    ImageAsset,
    Notification,
    PromptOperation,
    User,
)


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


@pytest.fixture()
def db() -> Generator:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    with (
        patch("app.main.ensure_bucket"),
        patch("app.main.seed_users_if_empty"),
    ):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


def _make_user(db, *, role: str = "user") -> User:
    user = User(
        username=f"g1a_{role}_{_suffix()}",
        password_hash=hash_password("test-pass-not-used"),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_asset(db, owner: User) -> ImageAsset:
    asset = ImageAsset(
        owner_id=owner.id,
        bucket="aigc-images",
        object_key=f"tests/g1a/{_suffix()}.png",
        mime="image/png",
        size_bytes=12,
        width=64,
        height=64,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def _make_op(db, owner: User, op_type: str) -> PromptOperation:
    op = PromptOperation(
        user_id=owner.id,
        op_type=op_type,
        input_text="hello" if op_type == "optimize" else None,
        output_prompt="out",
        status="succeeded",
    )
    db.add(op)
    db.commit()
    db.refresh(op)
    return op


def _as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _cleanup(db, *users: User) -> None:
    """只清本测试创建的用户及其关联，绝不碰 demo。"""
    ids = [u.id for u in users if u is not None]
    if not ids:
        return
    task_ids = list(
        db.scalars(select(GenerationTask.id).where(GenerationTask.user_id.in_(ids))).all()
    )
    if task_ids:
        db.execute(delete(Notification).where(Notification.task_id.in_(task_ids)))
        db.execute(
            delete(GenerationTaskAsset).where(GenerationTaskAsset.task_id.in_(task_ids))
        )
        db.execute(delete(GenerationTask).where(GenerationTask.id.in_(task_ids)))
    db.execute(delete(Notification).where(Notification.user_id.in_(ids)))
    db.execute(delete(PromptOperation).where(PromptOperation.user_id.in_(ids)))
    db.execute(delete(ImageAsset).where(ImageAsset.owner_id.in_(ids)))
    db.execute(delete(User).where(User.id.in_(ids)))
    db.commit()


def test_unauthenticated_returns_401(client: TestClient) -> None:
    app.dependency_overrides.clear()
    r = client.get("/api/generations")
    assert r.status_code == 401
    r2 = client.post(
        "/api/generations",
        json={"prompt": "x", "mode": "t2i", "size": "2048x2048"},
    )
    assert r2.status_code == 401
    r3 = client.patch("/api/generations/1/review", json={"review_status": "usable"})
    assert r3.status_code == 401


def test_reject_others_asset(client: TestClient, db) -> None:
    owner = _make_user(db)
    other = _make_user(db)
    foreign = _make_asset(db, other)
    _as(owner)
    try:
        r = client.post(
            "/api/generations",
            json={
                "prompt": "g1a product only",
                "mode": "i2i",
                "size": "2048x2048",
                "product_asset_id": foreign.id,
            },
        )
        assert r.status_code == 400
        assert "不属" in r.json()["detail"] or "参考图" in r.json()["detail"]
    finally:
        _cleanup(db, owner, other)


def test_i2i_product_and_scene_roles(client: TestClient, db) -> None:
    user = _make_user(db)
    product = _make_asset(db, user)
    scene = _make_asset(db, user)
    _as(user)
    try:
        r = client.post(
            "/api/generations",
            json={
                "prompt": "g1a both",
                "mode": "i2i",
                "size": "2048x2048",
                "product_asset_id": product.id,
                "scene_asset_id": scene.id,
                "keep_features": "logo color",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        roles = {a["role"] for a in body["assets"]}
        assert roles == {"product", "scene"}
        assert body["params"].get("keep_features") == "logo color"
        assert body["legacy_unlabeled"] is False
        assert body["review_status"] == "unreviewed"

        r2 = client.post(
            "/api/generations",
            json={
                "prompt": "g1a product only",
                "mode": "i2i",
                "size": "2048x2048",
                "product_asset_id": product.id,
            },
        )
        assert r2.status_code == 200, r2.text
        roles2 = {a["role"] for a in r2.json()["assets"]}
        assert roles2 == {"product"}
    finally:
        _cleanup(db, user)


def test_input_asset_ids_compat_writes_product_scene(client: TestClient, db) -> None:
    user = _make_user(db)
    a0 = _make_asset(db, user)
    a1 = _make_asset(db, user)
    _as(user)
    try:
        r = client.post(
            "/api/generations",
            json={
                "prompt": "g1a legacy ids",
                "mode": "i2i",
                "size": "2048x2048",
                "input_asset_ids": [a0.id, a1.id],
            },
        )
        assert r.status_code == 200, r.text
        by_pos = sorted(r.json()["assets"], key=lambda x: x["id"])
        # 按创建顺序：position 0 product, 1 scene
        assets = r.json()["assets"]
        assert len(assets) == 2
        role_by_id = {a["id"]: a["role"] for a in assets}
        assert role_by_id[a0.id] == "product"
        assert role_by_id[a1.id] == "scene"
        assert r.json()["legacy_unlabeled"] is False
        _ = by_pos
    finally:
        _cleanup(db, user)


def test_legacy_unlabeled_for_old_input_role(client: TestClient, db) -> None:
    user = _make_user(db)
    asset = _make_asset(db, user)
    task = GenerationTask(
        user_id=user.id,
        mode="i2i",
        prompt="old unlabeled",
        params={"size": "2048x2048"},
        status="succeeded",
        review_status="unreviewed",
    )
    db.add(task)
    db.flush()
    db.add(
        GenerationTaskAsset(
            task_id=task.id,
            asset_id=asset.id,
            role="input",
            position=0,
        )
    )
    db.commit()
    task_id = task.id
    _as(user)
    try:
        r = client.get(f"/api/generations/{task_id}")
        assert r.status_code == 200, r.text
        assert r.json()["legacy_unlabeled"] is True
        assert any(a["role"] == "input" for a in r.json()["assets"])
    finally:
        _cleanup(db, user)


def test_failed_cannot_mark_usable(client: TestClient, db) -> None:
    user = _make_user(db)
    task = GenerationTask(
        user_id=user.id,
        mode="t2i",
        prompt="failed one",
        params={},
        status="failed",
        review_status="unreviewed",
        error_message="boom",
    )
    db.add(task)
    db.commit()
    task_id = task.id
    _as(user)
    try:
        r = client.patch(
            f"/api/generations/{task_id}/review",
            json={"review_status": "usable", "review_reason": "nope"},
        )
        assert r.status_code == 400
        # 仍保持 unreviewed
        r2 = client.get(f"/api/generations/{task_id}")
        assert r2.json()["review_status"] == "unreviewed"
    finally:
        _cleanup(db, user)


def test_succeeded_can_review_and_admin_others(client: TestClient, db) -> None:
    owner = _make_user(db)
    admin = _make_user(db, role="admin")
    stranger = _make_user(db)
    task = GenerationTask(
        user_id=owner.id,
        mode="t2i",
        prompt="ok",
        params={},
        status="succeeded",
        review_status="unreviewed",
    )
    db.add(task)
    db.commit()
    task_id = task.id
    try:
        _as(owner)
        r = client.patch(
            f"/api/generations/{task_id}/review",
            json={"review_status": "usable", "review_reason": "结构可接受"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["review_status"] == "usable"
        assert body["review_reason"] == "结构可接受"
        assert body["reviewed_by_id"] == owner.id
        assert body["reviewed_at"] is not None

        _as(stranger)
        r404 = client.patch(
            f"/api/generations/{task_id}/review",
            json={"review_status": "needs_revision"},
        )
        assert r404.status_code == 404

        _as(admin)
        r_admin = client.patch(
            f"/api/generations/{task_id}/review",
            json={"review_status": "needs_revision", "review_reason": "颜色偏差"},
        )
        assert r_admin.status_code == 200, r_admin.text
        assert r_admin.json()["review_status"] == "needs_revision"
        assert r_admin.json()["reviewed_by_id"] == admin.id

        # admin 详情带出审核字段 + product/scene 过滤已放开（此处无资产也 OK）
        detail = client.get(f"/api/admin/generations/{task_id}")
        assert detail.status_code == 200, detail.text
        d = detail.json()
        assert d["review_status"] == "needs_revision"
        assert d["reviewed_by_id"] == admin.id
        assert "legacy_unlabeled" in d
    finally:
        _cleanup(db, owner, admin, stranger)


def test_op_ownership_and_type(client: TestClient, db) -> None:
    user = _make_user(db)
    other = _make_user(db)
    own_reverse = _make_op(db, user, "reverse")
    own_optimize = _make_op(db, user, "optimize")
    foreign_reverse = _make_op(db, other, "reverse")
    wrong_type = _make_op(db, user, "optimize")  # 当 reverse 用
    product = _make_asset(db, user)
    _as(user)
    try:
        bad_owner = client.post(
            "/api/generations",
            json={
                "prompt": "op foreign",
                "mode": "i2i",
                "size": "2048x2048",
                "product_asset_id": product.id,
                "reverse_op_id": foreign_reverse.id,
            },
        )
        assert bad_owner.status_code == 400

        bad_type = client.post(
            "/api/generations",
            json={
                "prompt": "op wrong type",
                "mode": "i2i",
                "size": "2048x2048",
                "product_asset_id": product.id,
                "reverse_op_id": wrong_type.id,
            },
        )
        assert bad_type.status_code == 400

        ok = client.post(
            "/api/generations",
            json={
                "prompt": "op ok",
                "mode": "i2i",
                "size": "2048x2048",
                "product_asset_id": product.id,
                "reverse_op_id": own_reverse.id,
                "optimize_op_id": own_optimize.id,
            },
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["reverse_op_id"] == own_reverse.id
        assert body["optimize_op_id"] == own_optimize.id
    finally:
        _cleanup(db, user, other)


def test_admin_lists_product_scene_assets(client: TestClient, db) -> None:
    owner = _make_user(db)
    admin = _make_user(db, role="admin")
    product = _make_asset(db, owner)
    scene = _make_asset(db, owner)
    task = GenerationTask(
        user_id=owner.id,
        mode="i2i",
        prompt="admin assets",
        params={},
        status="succeeded",
        review_status="unreviewed",
    )
    db.add(task)
    db.flush()
    db.add(
        GenerationTaskAsset(
            task_id=task.id, asset_id=product.id, role="product", position=0
        )
    )
    db.add(
        GenerationTaskAsset(
            task_id=task.id, asset_id=scene.id, role="scene", position=1
        )
    )
    db.commit()
    task_id = task.id
    _as(admin)
    try:
        r = client.get(f"/api/admin/generations/{task_id}")
        assert r.status_code == 200, r.text
        roles = {a["role"] for a in r.json()["assets"]}
        assert "product" in roles
        assert "scene" in roles
    finally:
        _cleanup(db, owner, admin)
