"""活动在 PostgreSQL 里的创建、幂等启动、领取和中断。没有库就跳过。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

import pytest
from sqlalchemy import func, select, text

from app.core.db import SessionLocal
from app.models import ImageAsset
from app.models.campaign import CampaignRun, ContentVariant, PipelineStep, VariantAsset
from app.services import storage
from app.models.copywriting import CopywritingOperation
from app.models.product import OwnedProduct, ProductFactVersion
from app.models.source import CollectionConfig, CollectionRun, SourceItem
from app.pipeline_worker import (
    claim_pending,
    finish_step,
    mark_unacked,
    process_step,
    reconcile_expired,
    renew_lease,
    tick,
)
from app.schemas.copywriting import GeneratedContent
from app.services.risk_lexicon import check_content
from tests.conftest import as_user, cleanup, make_asset, make_user, pg_available

BACKEND = Path(__file__).resolve().parents[1]
PRODUCT_PHOTO = Path(__file__).resolve().parent / "fixtures" / "canvas-tote.jpg"
SAFE_BODY = "材质是帆布。"
REJECTED_BODY = "帆布包适合通勤，没有未确认的功能"


def _content(body: str = SAFE_BODY) -> tuple[GeneratedContent, object]:
    content = GeneratedContent(title="帆布托特", body=body, hashtags=["箱包"])
    return content, check_content(content)


def _seed(db):
    user = make_user(db)
    asset = make_asset(db, user)
    now = datetime.now(timezone.utc)
    product = OwnedProduct(
        owner_id=user.id,
        sku=f"sku-{user.id}",
        name="帆布托特",
        active=True,
        primary_asset_id=asset.id,
    )
    db.add(product)
    db.flush()
    fact = ProductFactVersion(
        product_id=product.id,
        facts={"material": "帆布", "waterproof": "needs_confirmation"},
        claim_evidence={},
        version=1,
        )
    db.add(fact)
    config = CollectionConfig(
        owner_id=user.id,
        provider="fixture",
        item_kind="product",
        category="箱包",
        query="箱包",
        window="7d",
        sort_metric="sample_order",
        max_items=100,
        schedule="manual",
        enabled=True,
        provider_settings={},
    )
    db.add(config)
    db.flush()
    run = CollectionRun(
        config_id=config.id,
        status="succeeded",
        actual_count=2,
        scope_description="内部箱包样本，不是全平台榜",
        started_at=now,
        finished_at=now,
    )
    db.add(run)
    db.flush()
    first = SourceItem(
        run_id=run.id,
        platform="fixture",
        item_kind="product",
        external_id=f"bag-a-{user.id}",
        url="https://fixture.internal/bags/a",
        title="棕斜挎",
        source_rank=1,
        observed_at=now,
        media_refs=[],
        raw_metrics={"dataset": "internal_fixture"},
        rights_scope="internal_dev_only",
    )
    second = SourceItem(
        run_id=run.id,
        platform="fixture",
        item_kind="product",
        external_id=f"bag-b-{user.id}",
        url="https://fixture.internal/bags/b",
        title="黑托特",
        source_rank=2,
        observed_at=now,
        media_refs=[],
        raw_metrics={"dataset": "internal_fixture"},
        rights_scope="internal_dev_only",
    )
    db.add(first)
    db.add(second)
    db.commit()
    return user, product, fact, first, second


def _ops(db, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(CopywritingOperation).where(CopywritingOperation.user_id == user_id)
        )
        or 0
    )


@pytest.fixture()
def db():
    if not pg_available():
        pytest.skip("PostgreSQL unavailable")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_upgrade_from_0004_to_head():
    if not pg_available():
        pytest.skip("PostgreSQL unavailable")
    import psycopg2

    from app.core.config import settings

    name = "mig_" + uuid.uuid4().hex[:16]
    admin = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD or None,
        dbname="postgres",
    )
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()
    env = os.environ.copy()
    env.update(
        {
            "POSTGRES_HOST": settings.POSTGRES_HOST,
            "POSTGRES_PORT": str(settings.POSTGRES_PORT),
            "POSTGRES_USER": settings.POSTGRES_USER,
            "POSTGRES_PASSWORD": settings.POSTGRES_PASSWORD,
            "POSTGRES_DB": name,
        }
    )
    env.pop("LD_PRELOAD", None)

    alembic = Path(sys.executable).parent / ("alembic.exe" if os.name == "nt" else "alembic")

    def upgrade(revision: str) -> None:
        process = subprocess.run(
            [str(alembic), "upgrade", revision],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
        )
        assert process.returncode == 0, process.stderr[-1000:]

    def version() -> str:
        conn = psycopg2.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD or None,
            dbname=name,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version_num FROM alembic_version")
                return cur.fetchone()[0]
        finally:
            conn.close()

    upgrade("0004")
    assert version() == "0004_demo_ops"
    legacy_name = "legacy-" + uuid.uuid4().hex[:8]
    remembered = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD or None,
        dbname=name,
    )
    remembered.autocommit = True
    with remembered.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'user') RETURNING id",
            (legacy_name, "hash"),
        )
        user_id = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO generation_tasks (user_id, mode, prompt, params, status)
            VALUES (%s, 't2i', 'legacy-prompt-keep', '{}'::jsonb, 'succeeded')
            RETURNING id
            """,
            (user_id,),
        )
        task_id = cur.fetchone()[0]
    remembered.close()
    upgrade("0010_publish_jobs")
    stored = psycopg2.connect(
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD or None, dbname=name,
    )
    with stored.cursor() as cur:
        cur.execute("INSERT INTO owned_products (owner_id, sku, name, active) VALUES (%s, 'legacy-sku', '存量商品', true) RETURNING id", (user_id,))
        product_id = cur.fetchone()[0]
        cur.execute("INSERT INTO product_fact_versions (product_id, facts, claim_evidence, version) VALUES (%s, '{}'::jsonb, '{}'::jsonb, 1) RETURNING id", (product_id,))
        fact_id = cur.fetchone()[0]
        cur.execute("INSERT INTO campaigns (owner_id, product_id, fact_version_id, brief, selected_source_item_ids, status) VALUES (%s, %s, %s, '{}'::jsonb, '[]'::jsonb, 'approved') RETURNING id", (user_id, product_id, fact_id))
        campaign_id = cur.fetchone()[0]
        cur.execute("INSERT INTO content_variants (campaign_id, platform, content_type, version, hashtags, status, fact_version_id) VALUES (%s, 'douyin', 'video', 1, '[]'::jsonb, 'approved', %s) RETURNING id", (campaign_id, fact_id))
        variant_id = cur.fetchone()[0]
        cur.execute("INSERT INTO variant_reviews (variant_id, version, reviewer_id, decision, copy_snapshot, asset_order, fact_version_id, fact_snapshot, qc_snapshot) VALUES (%s, 1, %s, 'approved', '{}'::jsonb, '[]'::jsonb, %s, '{}'::jsonb, '{}'::jsonb) RETURNING id", (variant_id, user_id, fact_id))
        review_id = cur.fetchone()[0]
        cur.execute("INSERT INTO platform_connections (owner_id, platform, purpose, external_account_id, status, scope_set) VALUES (%s, 'douyin', 'publish', 'legacy-open-id', 'connected', '[]'::jsonb) RETURNING id", (user_id,))
        connection_id = cur.fetchone()[0]
        for status, bound_connection in (("uploaded", None), ("create_accepted", connection_id)):
            cur.execute(
                """INSERT INTO publish_jobs
                   (owner_id, campaign_id, platform, connection_id, variant_id, version, review_id,
                    scheduled_at, status, copy_snapshot, asset_order, missing_requirements, readiness,
                    provider_request_id, provider_video_id, attempt, result)
                   VALUES (%s, %s, 'douyin', %s, %s, 1, %s,
                           now(), %s, '{}'::jsonb, '[]'::jsonb, '[]'::jsonb, 'ready',
                           'legacy-request', 'legacy-video', 1, '{}'::jsonb)""",
                (user_id, campaign_id, bound_connection, variant_id, review_id, status),
            )
    stored.commit()
    stored.close()
    upgrade("0012_publish_oauth")
    pending = psycopg2.connect(
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER, password=settings.POSTGRES_PASSWORD or None, dbname=name,
    )
    with pending.cursor() as cur:
        for state_hash in ("a" * 64, "b" * 64):
            cur.execute(
                """INSERT INTO publish_oauth_states
                   (state_hash, owner_id, session_hash, client_key, callback_uri, expires_at)
                   VALUES (%s, %s, %s, 'old-app', 'https://example.test/callback', now() + interval '5 minutes')""",
                (state_hash, user_id, "s" * 64),
            )
        cur.execute(
            """INSERT INTO platform_connections
               (owner_id, platform, purpose, app_client_key, external_account_id, status,
                scope_set, credential_origin, credential_version)
               VALUES (%s, 'douyin', 'publish', 'old-app', 'managed-open-id', 'connected',
                       '["video.create.bind"]'::jsonb, 'managed', 4) RETURNING id""",
            (user_id,),
        )
        managed_connection_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO publish_oauth_secrets
               (connection_id, owner_id, client_key, open_id, key_version, access_ciphertext,
                refresh_ciphertext, access_expires_at, refresh_expires_at, credential_version)
               VALUES (%s, %s, 'old-app', 'managed-open-id', 'v1', %s, %s,
                       now() + interval '1 day', now() + interval '2 days', 4) RETURNING id""",
            (managed_connection_id, user_id, b"old-access-ciphertext", b"old-refresh-ciphertext"),
        )
        secret_id = cur.fetchone()[0]
        cur.execute("UPDATE platform_connections SET credential_ref = %s WHERE id = %s",
                    (f"db:{secret_id}", managed_connection_id))
    pending.commit()
    pending.close()
    upgrade("head")
    assert version() == "0014_operation_plans"
    check = psycopg2.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD or None,
        dbname=name,
    )
    with check.cursor() as cur:
        cur.execute(
            """
            SELECT u.username, g.prompt, g.review_status
            FROM users u
            JOIN generation_tasks g ON g.user_id = u.id
            WHERE g.id = %s
            """,
            (task_id,),
        )
        row = cur.fetchone()
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'pipeline_steps' AND column_name = 'claim_token'
            """
        )
        assert cur.fetchone()[0] == "claim_token"
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'publish_jobs' AND indexname = 'uq_publish_jobs_active_connection'
            """
        )
        assert cur.fetchone()[0] == "uq_publish_jobs_active_connection"
        cur.execute("SELECT status, phase, provider_request_id, provider_video_id, cover_image_id, video_upload_id FROM publish_jobs ORDER BY id")
        legacy_jobs = cur.fetchall()
        assert [(row[0], row[1]) for row in legacy_jobs] == [
            ("uploaded", "legacy_unknown"), ("create_accepted", "legacy_unknown"),
        ]
        assert all(row[2:4] == ("legacy-request", "legacy-video") and row[4:] == (None, None) for row in legacy_jobs)
        cur.execute("SELECT app_client_key, credential_origin, credential_version, credential_ref, last_authorization_attempt_id FROM platform_connections WHERE id = %s", (connection_id,))
        assert cur.fetchone() == ("", "deployment", 0, None, 0)
        cur.execute("SELECT to_regclass('publish_oauth_states'), to_regclass('publish_oauth_secrets')")
        assert cur.fetchone() == ("publish_oauth_states", "publish_oauth_secrets")
        cur.execute("SELECT attempt_id, consumed_at IS NOT NULL FROM publish_oauth_states ORDER BY attempt_id")
        prior_states = cur.fetchall()
        assert len(prior_states) == 2 and prior_states[0][0] < prior_states[1][0]
        assert all(consumed for _, consumed in prior_states)
        cur.execute(
            """INSERT INTO publish_oauth_states
               (state_hash, owner_id, session_hash, client_key, callback_uri, expires_at)
               VALUES (%s, %s, %s, 'old-app', 'https://example.test/callback', now() + interval '5 minutes')
               RETURNING attempt_id, consumed_at""",
            ("c" * 64, user_id, "s" * 64),
        )
        new_attempt_id, new_consumed = cur.fetchone()
        assert new_attempt_id > prior_states[-1][0] and new_consumed is None
        cur.execute("""SELECT c.status, c.credential_version, c.last_authorization_attempt_id,
                             c.credential_ref, s.access_ciphertext, s.refresh_ciphertext
                       FROM platform_connections c JOIN publish_oauth_secrets s ON s.connection_id = c.id
                       WHERE c.id = %s""", (managed_connection_id,))
        preserved_status, preserved_version, preserved_attempt, preserved_ref, access_cipher, refresh_cipher = cur.fetchone()
        assert (preserved_status, preserved_version, preserved_attempt, preserved_ref) == (
            "connected", 4, 0, f"db:{secret_id}",
        )
        assert bytes(access_cipher) == b"old-access-ciphertext"
        assert bytes(refresh_cipher) == b"old-refresh-ciphertext"
        cur.execute("SAVEPOINT legacy_unique")
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute("INSERT INTO platform_connections (owner_id, platform, purpose, external_account_id, status, scope_set) VALUES (%s, 'douyin', 'publish', 'legacy-open-id', 'connected', '[]'::jsonb)", (user_id,))
        cur.execute("ROLLBACK TO SAVEPOINT legacy_unique")
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'publish_jobs' AND column_name = 'asset_order'
            """
        )
        assert cur.fetchone()[0] == "asset_order"
    check.close()
    assert row == (legacy_name, "legacy-prompt-keep", "unreviewed")


def test_create_rejects_foreign_source_and_persists_deps(client, db):
    user, product, fact, first, second = _seed(db)
    other = make_user(db)
    try:
        as_user(user)
        missing = client.post(
            "/api/campaigns",
            json={
                "product_id": product.id,
                "fact_version_id": fact.id,
                "source_item_ids": [first.id, 999999],
            },
        )
        assert missing.status_code == 404
        wrong_fact = client.post(
            "/api/campaigns",
            json={
                "product_id": product.id,
                "fact_version_id": fact.id + 1000,
                "source_item_ids": [first.id],
            },
        )
        assert wrong_fact.status_code == 404
        created = client.post(
            "/api/campaigns",
            json={
                "product_id": product.id,
                "fact_version_id": fact.id,
                "source_item_ids": [second.id, first.id],
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["status"] == "draft"
        assert body["fact_version_id"] == fact.id
        assert body["selected_source_item_ids"] == [first.id]
        assert body["brief"]["execute"][0]["source_item_id"] == first.id
        assert body["brief"]["execute"][0]["publish_body"] is None
        platforms = {item["platform"] for item in body["variants"]}
        assert platforms == {"douyin", "xiaohongshu"}
        deps = {step["step_key"]: step["depends_on"] for step in body["run"]["steps"]}
        assert deps["brief"] == []
        assert deps["copy:douyin"] == ["brief"]
        assert deps["video:douyin"] == ["image:douyin"]
        assert len(deps) == 6
        as_user(other)
        hidden = client.get(f"/api/campaigns/{body['id']}")
        assert hidden.status_code == 404
    finally:
        cleanup(db, user, other)


def test_start_is_idempotent(client, db):
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={
                "product_id": product.id,
                "fact_version_id": fact.id,
                "source_item_ids": [first.id],
                "generation_budget": 4,
            },
        )
        campaign_id = created.json()["id"]
        first_start = client.post(
            f"/api/campaigns/{campaign_id}/start",
            headers={"Idempotency-Key": "start-1"},
        )
        second_start = client.post(
            f"/api/campaigns/{campaign_id}/start",
            headers={"Idempotency-Key": "start-1"},
        )
        third = client.post(f"/api/campaigns/{campaign_id}/start")
        assert first_start.status_code == 200
        assert first_start.json()["run"]["id"] == second_start.json()["run"]["id"] == third.json()["run"]["id"]
        assert first_start.json()["status"] == "generating"
        assert db.scalar(select(func.count()).select_from(CampaignRun).where(CampaignRun.campaign_id == campaign_id)) == 1
        assert len(first_start.json()["run"]["steps"]) == len(third.json()["run"]["steps"]) == 6
    finally:
        cleanup(db, user)


def test_two_workers_and_dependency(client, db):
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        client.post(f"/api/campaigns/{created.json()['id']}/start", headers={"Idempotency-Key": "dual"})
        left = SessionLocal()
        right = SessionLocal()
        try:
            first_claim = claim_pending(left, commit=False)
            second_claim = claim_pending(right, commit=False)
            assert first_claim is not None
            assert second_claim is None
            left.commit()
            right.rollback()
        finally:
            left.close()
            right.close()
        step = db.get(PipelineStep, first_claim.step_id)
        db.refresh(step)
        assert step.step_key == "brief"
        assert step.attempt == 1
        assert step.heartbeat_at is not None
        assert claim_pending(db) is None
    finally:
        cleanup(db, user)


def test_copy_lands_and_traces_source(client, db, monkeypatch):
    user, product, fact, first, second = _seed(db)
    calls = {"n": 0}

    def fake_copy(**kwargs):
        calls["n"] += 1
        assert kwargs["facts"]["waterproof"] == "needs_confirmation"
        assert "防水" not in kwargs["facts"].get("material", "")
        assert kwargs["source_references"][0]["source_item_id"] == first.id
        assert "source_item_id" not in kwargs["facts"]
        assert kwargs["generation_requirements"] == "突出通勤场景"
        return _content()

    monkeypatch.setattr("app.pipeline_worker.produce_copy", fake_copy)
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={
                "product_id": product.id,
                "fact_version_id": fact.id,
                "source_item_ids": [second.id, first.id],
                "generation_requirements": "突出通勤场景",
            },
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "copy"})
        for _ in range(6):
            tick(db)
            detail = client.get(f"/api/campaigns/{campaign_id}").json()
            by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
            if (
                by_key["copy:douyin"]["status"] == "succeeded"
                and by_key["copy:xiaohongshu"]["status"] == "succeeded"
            ):
                break
        assert by_key["brief"]["status"] == "succeeded"
        assert by_key["copy:douyin"]["status"] == "succeeded"
        assert by_key["copy:xiaohongshu"]["status"] == "succeeded"
        assert by_key["copy:douyin"]["provider_request_id"] is None
        assert by_key["copy:douyin"]["copywriting_operation_id"]
        assert by_key["copy:douyin"]["local_request_id"]
        assert by_key["copy:douyin"]["local_request_id"] != str(by_key["copy:douyin"]["copywriting_operation_id"])
        douyin = next(item for item in detail["variants"] if item["platform"] == "douyin")
        xhs = next(item for item in detail["variants"] if item["platform"] == "xiaohongshu")
        assert douyin["title"] == "帆布托特"
        assert douyin["body"] == SAFE_BODY
        assert douyin["hashtags"] == ["箱包"]
        assert xhs["body"] == SAFE_BODY
        assert calls["n"] == 2
        assert _ops(db, user.id) == 2
        assert detail["selected_source_item_ids"] == [first.id]
        assert detail["fact_version_id"] == fact.id
        stored = db.get(CopywritingOperation, by_key["copy:douyin"]["copywriting_operation_id"])
        assert stored.generated_content["body"] == SAFE_BODY
        assert stored.product_facts["product_name"] == "帆布托特"
    finally:
        cleanup(db, user)


def test_crash_without_provider_id_is_not_resubmitted(client, db, monkeypatch):
    user, product, fact, first, _second = _seed(db)
    monkeypatch.setattr("app.pipeline_worker.produce_copy", lambda **_k: (_ for _ in ()).throw(AssertionError("不应再交")))
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        client.post(f"/api/campaigns/{created.json()['id']}/start", headers={"Idempotency-Key": "crash"})
        tick(db)  # brief
        claimed = claim_pending(db)
        assert claimed is not None
        copy_id = claimed.step_id
        step = db.get(PipelineStep, copy_id)
        assert step.step_key.startswith("copy:")
        assert step.local_request_id
        assert step.provider_request_id is None
        assert step.local_request_id != step.claim_token
        step.lease_until = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()
        assert finish_step(db, copy_id, claimed.token, status="succeeded", output={"late": True}) is False
        db.refresh(step)
        assert step.status == "running"
        assert "late" not in (step.output or {})
        unknown = mark_unacked(db)
        assert copy_id in unknown
        db.refresh(step)
        assert step.status == "unknown"
        assert step.claim_token is None
        assert step.attempt == 1
        assert finish_step(db, copy_id, claimed.token, status="succeeded", output={"late": True}) is False
        db.refresh(step)
        assert step.status == "unknown"
        assert _ops(db, user.id) == 0
        sibling = claim_pending(db, commit=False)
        assert sibling is None or sibling.step_id != copy_id
        db.rollback()
        db.refresh(step)
        assert step.status == "unknown"
        assert step.attempt == 1
    finally:
        cleanup(db, user)


def test_known_provider_id_is_reconciled(client, db, monkeypatch):
    user, product, fact, first, _second = _seed(db)
    monkeypatch.setattr(
        "app.pipeline_worker.produce_copy",
        lambda **_k: (_ for _ in ()).throw(AssertionError("对账不能再交")),
    )
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        client.post(f"/api/campaigns/{created.json()['id']}/start", headers={"Idempotency-Key": "recon"})
        tick(db)
        claimed = claim_pending(db)
        assert claimed is not None
        copy_id = claimed.step_id
        step = db.get(PipelineStep, copy_id)
        content, risk = _content()
        op = CopywritingOperation(
            user_id=user.id,
            input_asset_id=product.primary_asset_id,
            platform=step.variant_platform,
            product_facts={"waterproof": "needs_confirmation"},
            generated_content=content.model_dump(),
            risk_result=risk.model_dump(),
            status="succeeded",
        )
        db.add(op)
        db.flush()
        step.copywriting_operation_id = op.id
        step.provider_request_id = "ark-req-77"
        step.lease_until = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()
        assert step.local_request_id != step.provider_request_id
        before = _ops(db, user.id)
        reconcile_expired(db)
        db.refresh(step)
        assert step.status == "succeeded"
        assert step.output["reconciled"] is True
        assert step.provider_request_id == "ark-req-77"
        assert _ops(db, user.id) == before == 1
    finally:
        cleanup(db, user)


def test_budget_stops_second_copy(client, db, monkeypatch):
    calls = {"n": 0}

    def fake_copy(**_kwargs):
        calls["n"] += 1
        return _content()

    monkeypatch.setattr("app.pipeline_worker.produce_copy", fake_copy)
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={
                "product_id": product.id,
                "fact_version_id": fact.id,
                "source_item_ids": [first.id],
                "generation_budget": 1,
            },
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "budget"})
        for _ in range(5):
            tick(db)
        detail = client.get(f"/api/campaigns/{campaign_id}").json()
        by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
        assert calls["n"] == 1
        assert by_key["copy:douyin"]["status"] == "succeeded"
        assert by_key["copy:xiaohongshu"]["status"] == "skipped"
        assert by_key["copy:xiaohongshu"]["error_code"] == "budget_exceeded"
        assert detail["status"] == "blocked"
        assert _ops(db, user.id) == 1
    finally:
        cleanup(db, user)


def test_copy_failure_blocks_only_its_branch(client, db, monkeypatch):
    def fake_copy(**kwargs):
        if kwargs["platform"] == "douyin":
            raise RuntimeError("生图上游先失败")
        return _content()

    monkeypatch.setattr("app.pipeline_worker.produce_copy", fake_copy)
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        client.post(f"/api/campaigns/{created.json()['id']}/start", headers={"Idempotency-Key": "branch"})
        for _ in range(6):
            tick(db)
            detail = client.get(f"/api/campaigns/{created.json()['id']}").json()
            by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
            if by_key["copy:douyin"]["status"] == "failed" and by_key["copy:xiaohongshu"]["status"] == "succeeded":
                break
        assert by_key["copy:douyin"]["status"] == "failed"
        assert by_key["image:douyin"]["status"] == "skipped"
        assert by_key["video:douyin"]["status"] == "skipped"
        assert by_key["copy:xiaohongshu"]["status"] == "succeeded"
        assert by_key["cards:xiaohongshu"]["status"] == "pending"
        stored = db.get(CopywritingOperation, by_key["copy:xiaohongshu"]["copywriting_operation_id"])
        assert stored.status == "succeeded"
    finally:
        cleanup(db, user)


def test_unconfirmed_waterproof_is_rejected(client, db, monkeypatch):
    monkeypatch.setattr("app.pipeline_worker.produce_copy", lambda **_k: _content("这只包防水"))
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        client.post(f"/api/campaigns/{created.json()['id']}/start", headers={"Idempotency-Key": "water"})
        tick(db)
        tick(db)
        detail = client.get(f"/api/campaigns/{created.json()['id']}").json()
        by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
        assert by_key["copy:douyin"]["status"] == "failed"
        assert by_key["copy:douyin"]["error_code"] == "unconfirmed_fact"
        assert by_key["image:douyin"]["status"] == "skipped"
        op = db.scalar(select(CopywritingOperation).where(CopywritingOperation.user_id == user.id))
        assert op.status == "failed"
        assert op.generated_content is None
    finally:
        cleanup(db, user)


def test_ffmpeg_outputs_are_stored_in_order(client, db, monkeypatch, tmp_path):
    user, product, fact, first, _second = _seed(db)
    owned = db.get(ImageAsset, product.primary_asset_id)
    blob: dict[str, bytes] = {owned.object_key: PRODUCT_PHOTO.read_bytes()}

    def put_bytes(key, data, content_type):
        del content_type
        blob[key] = data

    monkeypatch.setattr(storage, "put_bytes", put_bytes)
    monkeypatch.setattr(storage, "get_bytes", lambda key: blob[key])
    monkeypatch.setattr(storage, "object_exists", lambda key: key in blob)
    monkeypatch.setattr(storage, "delete_object", lambda key: blob.pop(key, None))
    monkeypatch.setattr("app.pipeline_worker.produce_copy", lambda **_k: _content())
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "media"})
        detail = {}
        for _ in range(12):
            tick(db)
            detail = client.get(f"/api/campaigns/{campaign_id}").json()
            by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
            if by_key["video:douyin"]["status"] == "succeeded" and by_key["cards:xiaohongshu"]["status"] == "succeeded":
                break
        by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
        assert by_key["video:douyin"]["status"] == "succeeded", by_key["video:douyin"]
        cards_step = by_key["cards:xiaohongshu"]
        assert cards_step["status"] == "succeeded", cards_step
        assert cards_step["error_code"] is None
        video_out = by_key["video:douyin"]["output"]
        probe = video_out["probe"]
        assert probe["native_model_video"] is False
        assert probe["qc"] == "needs_review"
        assert probe["codec"] == "h264"
        assert probe["audio_codec"] == "aac"
        assert probe["audio_owned"] is True
        assert probe["tool"] == "ffprobe"
        assert 15 <= probe["duration"] <= 30.4
        painted = "".join(video_out["lines"])
        assert "帆布" in painted
        assert SAFE_BODY in painted
        assert "方舟" not in painted
        assert "未确认" not in painted
        assert "通勤" not in painted
        assert "不能写成卖点" not in painted
        assert video_out["qc_issue"] is None
        assert any("防水" in note for note in video_out["review_notes"])
        assert video_out["title"] == "帆布托特"
        assert video_out["body"] == SAFE_BODY
        assert by_key["video:douyin"]["provider_request_id"] is None
        video_asset = db.get(ImageAsset, video_out["asset_id"])
        assert blob[video_asset.object_key][:8] != b""
        assert video_asset.mime == "video/mp4"
        links = list(
            db.scalars(
                select(VariantAsset)
                .join(ContentVariant, VariantAsset.variant_id == ContentVariant.id)
                .where(ContentVariant.campaign_id == campaign_id, ContentVariant.platform == "xiaohongshu")
                .order_by(VariantAsset.position.asc())
            ).all()
        )
        assert links
        assert len(links) == 2
        assert [link.position for link in links] == list(range(len(links)))
        assert links[0].role == "cover"
        assert all(link.role == "card" for link in links[1:])
        card_out = by_key["cards:xiaohongshu"]["output"]
        assert card_out["qc_issue"] is None
        missing = card_out["missing"]
        assert card_out["supplements_required"] is False
        assert card_out["need_more_cards"] == 0
        assert missing == []
        purposes = [card["purpose"] for card in card_out["cards"]]
        assert len(purposes) == len(set(purposes))
        assert "方舟" not in "".join(card_out["lines"])
        assert "未确认" not in "".join(card_out["lines"])
        preview = tmp_path / "ops-preview"
        preview.mkdir(parents=True, exist_ok=True)
        for old in preview.iterdir():
            if old.is_file():
                old.unlink()
        (preview / "product.jpg").write_bytes(PRODUCT_PHOTO.read_bytes())
        (preview / "final.mp4").write_bytes(blob[video_asset.object_key])
        for link in links:
            asset = db.get(ImageAsset, link.asset_id)
            (preview / f"{link.position:02d}-{link.role}.png").write_bytes(blob[asset.object_key])
        douyin_cover = db.scalar(
            select(VariantAsset)
            .join(ContentVariant, VariantAsset.variant_id == ContentVariant.id)
            .where(ContentVariant.campaign_id == campaign_id, VariantAsset.role == "cover", ContentVariant.platform == "douyin")
        )
        if douyin_cover is not None:
            cover_asset = db.get(ImageAsset, douyin_cover.asset_id)
            (preview / "douyin-cover.png").write_bytes(blob[cover_asset.object_key])
        (preview / "campaign.json").write_text(
            json.dumps(
                {
                    "ark_live": "pending",
                    "copy_source": "测试替身，不是方舟或真实模型生成",
                    "audio": probe.get("audio_source"),
                    "audio_license": probe.get("audio_license"),
                    "complete_card_set": True,
                    "blocked": card_out["qc_issue"],
                    "missing": missing,
                    "note": "实际锁定事实只有商品名和材质，生成封面和一张材质卡；未补造尺寸、容量、承重或场景。未知防水不使用。文案来自测试替身。",
                    "campaign": detail,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        cleanup(db, user)


def test_ungrounded_scene_in_title_blocks_even_when_body_is_ok(client, db, monkeypatch):
    content = GeneratedContent(title="通勤托特", body=SAFE_BODY, hashtags=["箱包"])
    monkeypatch.setattr("app.pipeline_worker.produce_copy", lambda **_k: (content, check_content(content)))
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={
                "product_id": product.id,
                "fact_version_id": fact.id,
                "source_item_ids": [first.id],
                "generation_budget": 1,
            },
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "title-scene"})
        tick(db)
        tick(db)
        detail = client.get(f"/api/campaigns/{campaign_id}").json()
        by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
        assert by_key["copy:douyin"]["status"] == "failed"
        assert by_key["copy:douyin"]["error_code"] == "ungrounded"
        draft = by_key["copy:douyin"]["output"]["drafts"][0]
        assert draft["title"] == "通勤托特"
        assert draft["body"] == SAFE_BODY
        assert draft["issues"][0]["original"] == "通勤托特"
        assert not any(item["field"] == "hashtag" for item in draft["issues"])
        variant = next(item for item in detail["variants"] if item["platform"] == "douyin")
        assert variant["title"] is None
        assert by_key["image:douyin"]["status"] == "skipped"
    finally:
        cleanup(db, user)


def test_synthetic_facts_make_four_traceable_cards(client, db, monkeypatch):
    """隔离的测试规格。不是帆布托特的真实尺寸、容量或承重。"""
    synthetic = {
        "material": "测试材质",
        "size": "测试尺寸A",
        "capacity": "测试容量B",
        "load": "测试承重C",
        "waterproof": "needs_confirmation",
        "spec_status": "synthetic_not_a_real_product",
    }
    user, product, fact, first, _second = _seed(db)
    product.name = "测试样品-非真实商品"
    fact.facts = synthetic
    db.commit()
    owned = db.get(ImageAsset, product.primary_asset_id)
    blob: dict[str, bytes] = {owned.object_key: PRODUCT_PHOTO.read_bytes()}

    def put_bytes(key, data, content_type):
        del content_type
        blob[key] = data

    def copy(**kwargs):
        assert kwargs["facts"]["spec_status"] == "synthetic_not_a_real_product"
        content = GeneratedContent(title="测试样品-非真实商品", body="测试材质。", hashtags=["箱包"])
        return content, check_content(content)

    monkeypatch.setattr(storage, "put_bytes", put_bytes)
    monkeypatch.setattr(storage, "get_bytes", lambda key: blob[key])
    monkeypatch.setattr(storage, "object_exists", lambda key: key in blob)
    monkeypatch.setattr(storage, "delete_object", lambda key: blob.pop(key, None))
    monkeypatch.setattr("app.pipeline_worker.produce_copy", copy)
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "synthetic-cards"})
        detail = {}
        for _ in range(12):
            tick(db)
            detail = client.get(f"/api/campaigns/{campaign_id}").json()
            by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
            if by_key["cards:xiaohongshu"]["status"] in {"succeeded", "failed"}:
                break
        by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
        assert by_key["cards:xiaohongshu"]["status"] == "succeeded", by_key["cards:xiaohongshu"]
        card_out = by_key["cards:xiaohongshu"]["output"]
        assert card_out["qc_issue"] is None
        assert card_out["missing"] == []
        cards = card_out["cards"]
        assert len(cards) == 5
        assert cards[0]["role"] == "cover"
        content = cards[1:]
        assert len({card["purpose"] for card in content}) == 4
        assert len({card["body"] for card in content}) == 4
        for card in content:
            assert card["cites"]
            for key in card["cites"]:
                assert key in synthetic
                assert str(synthetic[key]) in card["body"]
        assert all("防水" not in card["body"] for card in cards)
        links = list(
            db.scalars(
                select(VariantAsset)
                .join(ContentVariant, VariantAsset.variant_id == ContentVariant.id)
                .where(ContentVariant.campaign_id == campaign_id, ContentVariant.platform == "xiaohongshu")
                .order_by(VariantAsset.position.asc())
            ).all()
        )
        assert [link.role for link in links] == ["cover", "card", "card", "card", "card"]
    finally:
        cleanup(db, user)
        left = db.scalar(select(OwnedProduct).where(OwnedProduct.name == "测试样品-非真实商品"))
        assert left is None


def test_two_workers_race_for_last_budget(client, db, monkeypatch):
    monkeypatch.setattr("app.pipeline_worker.produce_copy", lambda **_k: _content())
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={
                "product_id": product.id,
                "fact_version_id": fact.id,
                "source_item_ids": [first.id],
                "generation_budget": 1,
            },
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "race"})
        tick(db)
        found: list = []

        def grab() -> None:
            session = SessionLocal()
            try:
                found.append(claim_pending(session))
            finally:
                session.close()

        left = Thread(target=grab)
        right = Thread(target=grab)
        left.start()
        right.start()
        left.join()
        right.join()
        assert len([item for item in found if item is not None]) == 1
        steps = list(
            db.scalars(
                select(PipelineStep)
                .join(CampaignRun, PipelineStep.run_id == CampaignRun.id)
                .where(CampaignRun.campaign_id == campaign_id, PipelineStep.step_key.in_(["copy:douyin", "copy:xiaohongshu"]))
            ).all()
        )
        by_status = {step.status for step in steps}
        assert "running" in by_status
        assert "skipped" in by_status
        skipped = next(step for step in steps if step.status == "skipped")
        winner = next(step for step in steps if step.status == "running")
        assert skipped.error_code == "budget_exceeded"
        assert winner.local_request_id
        assert winner.provider_request_id is None
        assert winner.local_request_id != winner.claim_token
        run = db.get(CampaignRun, winner.run_id)
        assert run.budget_reserved == 1
    finally:
        cleanup(db, user)


def test_long_task_keeps_renewing_lease(client, db):
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        client.post(f"/api/campaigns/{created.json()['id']}/start", headers={"Idempotency-Key": "beat"})
        claimed = claim_pending(db)
        assert claimed is not None
        beats = []
        leases = []
        for _ in range(3):
            assert renew_lease(db, claimed.step_id, claimed.token)
            step = db.get(PipelineStep, claimed.step_id)
            db.refresh(step)
            beats.append(step.heartbeat_at)
            leases.append(step.lease_until)
        assert beats[0] <= beats[1] <= beats[2]
        assert leases[0] <= leases[1] <= leases[2]
        assert all(item is not None and item > datetime.now(timezone.utc) for item in leases)
        assert renew_lease(db, claimed.step_id, "wrong-token") is False
    finally:
        cleanup(db, user)


def _watch_heartbeats(step_id: int, stop: Event, bucket: list) -> None:
    while not stop.is_set():
        session = SessionLocal()
        try:
            row = session.execute(
                text("SELECT heartbeat_at FROM pipeline_steps WHERE id = :id"),
                {"id": step_id},
            ).first()
            session.rollback()
            if row and row[0] is not None:
                bucket.append(row[0])
        finally:
            session.close()
        stop.wait(0.05)


def _scan_expiry(stop: Event, found: list) -> None:
    while not stop.is_set():
        session = SessionLocal()
        try:
            found.extend(mark_unacked(session))
        finally:
            session.close()
        stop.wait(0.05)


def _run_with_scanner(db, step_id: int, token: str):
    stop = Event()
    beats: list = []
    expired: list = []
    watcher = Thread(target=_watch_heartbeats, args=(step_id, stop, beats))
    scanner = Thread(target=_scan_expiry, args=(stop, expired))
    watcher.start()
    scanner.start()
    try:
        process_step(db, step_id, token)
    finally:
        stop.set()
        watcher.join(timeout=3)
        scanner.join(timeout=3)
    return beats, expired


def test_copy_heartbeat_spans_leases_under_expiry_scan(client, db, monkeypatch):
    opened = {"tx": None}

    def slow_copy(**_k):
        opened["tx"] = db.in_transaction()
        time.sleep(1.6)
        return _content()

    monkeypatch.setattr("app.pipeline_worker.produce_copy", slow_copy)
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    monkeypatch.setattr("app.pipeline_worker.lease_seconds", 0.9)
    monkeypatch.setattr("app.pipeline_worker.heartbeat_interval", 0.2)
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "hb-copy"})
        brief = claim_pending(db)
        assert brief is not None
        process_step(db, brief.step_id, brief.token)
        claimed = claim_pending(db)
        assert claimed is not None
        beats, expired = _run_with_scanner(db, claimed.step_id, claimed.token)
        assert opened["tx"] is False
        assert claimed.step_id not in expired
        assert len(set(beats)) >= 3
        assert (max(beats) - min(beats)).total_seconds() > 0.9
        detail = client.get(f"/api/campaigns/{campaign_id}").json()
        by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
        copy_step = by_key["copy:douyin"]
        assert copy_step["status"] == "succeeded"
        assert copy_step["provider_request_id"] is None
        variant = next(item for item in detail["variants"] if item["platform"] == "douyin")
        assert variant["title"] == "帆布托特"
        assert variant["body"] == SAFE_BODY
        stored = db.get(CopywritingOperation, copy_step["copywriting_operation_id"])
        db.refresh(stored)
        assert stored.status == "succeeded"
        assert stored.generated_content["body"] == SAFE_BODY
    finally:
        db.rollback()
        cleanup(db, user)


def test_stopped_heartbeat_rejects_late_copy(client, db, monkeypatch):
    late_body = "迟到正文不应写入变体"
    finished = {"called": False}

    def slow_copy(**_k):
        time.sleep(1.1)
        finished["called"] = True
        return _content(late_body)

    monkeypatch.setattr("app.pipeline_worker.produce_copy", slow_copy)
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    monkeypatch.setattr("app.pipeline_worker.lease_seconds", 0.35)
    monkeypatch.setattr("app.pipeline_worker.heartbeat_interval", None)
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "late-copy"})
        brief = claim_pending(db)
        assert brief is not None
        process_step(db, brief.step_id, brief.token)
        claimed = claim_pending(db)
        assert claimed is not None
        _beats, expired = _run_with_scanner(db, claimed.step_id, claimed.token)
        assert finished["called"] is True
        assert claimed.step_id in expired
        fresh = SessionLocal()
        try:
            row = fresh.execute(
                text(
                    """
                    SELECT status, error_code, output::text, provider_request_id
                    FROM pipeline_steps WHERE id = :id
                    """
                ),
                {"id": claimed.step_id},
            ).one()
            assert row.status == "unknown"
            assert row.error_code == "submit_unacked"
            assert late_body not in (row.output or "")
            assert row.provider_request_id is None
            op = fresh.scalar(select(CopywritingOperation).where(CopywritingOperation.user_id == user.id))
            assert op is not None
            assert op.status == "processing"
            assert op.generated_content is None
            variant = fresh.scalar(
                select(ContentVariant).where(
                    ContentVariant.campaign_id == campaign_id,
                    ContentVariant.platform == "douyin",
                )
            )
            assert variant.title is None
            assert variant.body is None
        finally:
            fresh.close()
    finally:
        db.rollback()
        cleanup(db, user)


def _patch_storage(monkeypatch, raw: bytes, object_key: str):
    blob: dict[str, bytes] = {object_key: raw}

    def put_bytes(key, data, content_type):
        del content_type
        blob[key] = data

    monkeypatch.setattr(storage, "put_bytes", put_bytes)
    monkeypatch.setattr(storage, "get_bytes", lambda key: blob.get(key, b""))
    monkeypatch.setattr(storage, "object_exists", lambda key: key in blob)
    monkeypatch.setattr(storage, "delete_object", lambda key: blob.pop(key, None))
    return blob


def test_media_heartbeat_and_late_render_do_not_overwrite(client, db, monkeypatch, tmp_path):
    source = tmp_path / "bag.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x223344:s=64x64", "-frames:v", "1", str(source)],
        check=True,
        capture_output=True,
    )
    user, product, fact, first, _second = _seed(db)
    owned = db.get(ImageAsset, product.primary_asset_id)
    _patch_storage(monkeypatch, source.read_bytes(), owned.object_key)
    monkeypatch.setattr("app.pipeline_worker.produce_copy", lambda **_k: _content())
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    monkeypatch.setattr("app.pipeline_worker.lease_seconds", 120)
    monkeypatch.setattr("app.pipeline_worker.heartbeat_interval", 20)
    opened = {"tx": None}
    from app.services.media_render import render_cover as real_cover

    def slow_cover(*args, **kwargs):
        opened["tx"] = db.in_transaction()
        time.sleep(1.6)
        return real_cover(*args, **kwargs)

    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "hb-media"})
        for _ in range(3):
            claimed = claim_pending(db)
            assert claimed is not None
            process_step(db, claimed.step_id, claimed.token)
        monkeypatch.setattr("app.pipeline_worker.lease_seconds", 0.9)
        monkeypatch.setattr("app.pipeline_worker.heartbeat_interval", 0.2)
        monkeypatch.setattr("app.pipeline_worker.render_cover", slow_cover)
        image = claim_pending(db)
        assert image is not None
        beats, expired = _run_with_scanner(db, image.step_id, image.token)
        assert opened["tx"] is False
        assert image.step_id not in expired
        assert len(set(beats)) >= 3
        assert (max(beats) - min(beats)).total_seconds() > 0.9
        detail = client.get(f"/api/campaigns/{campaign_id}").json()
        by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
        assert by_key["image:douyin"]["status"] == "succeeded"
        douyin = next(item for item in detail["variants"] if item["platform"] == "douyin")
        assert douyin["title"] == "帆布托特"
        assert douyin["body"] == SAFE_BODY

        late = {"called": False}

        def slow_clip(_source, dest, **_kwargs):
            time.sleep(1.1)
            late["called"] = True
            Path(dest).write_bytes(b"late-video-should-not-persist")
            return {
                "width": 720,
                "height": 1280,
                "qc": "needs_review",
                "qc_reason": "late",
                "qc_issue": None,
                "unplaced": "",
                "lines": ["迟到画面"],
                "duration": 18,
                "tool": "ffprobe",
                "codec": "h264",
                "audio_codec": "aac",
                "native_model_video": False,
                "audio_owned": True,
            }

        monkeypatch.setattr("app.pipeline_worker.lease_seconds", 0.35)
        monkeypatch.setattr("app.pipeline_worker.heartbeat_interval", None)
        monkeypatch.setattr("app.pipeline_worker.render_story_clip", slow_clip)
        video = claim_pending(db)
        assert video is not None
        _beats, expired = _run_with_scanner(db, video.step_id, video.token)
        assert late["called"] is True
        assert video.step_id in expired
        fresh = SessionLocal()
        try:
            row = fresh.execute(
                text("SELECT status, error_code, output::text FROM pipeline_steps WHERE id = :id"),
                {"id": video.step_id},
            ).one()
            assert row.status == "unknown"
            assert row.error_code == "submit_unacked"
            assert "迟到画面" not in (row.output or "")
            variant = fresh.scalar(
                select(ContentVariant).where(
                    ContentVariant.campaign_id == campaign_id,
                    ContentVariant.platform == "douyin",
                )
            )
            assert variant.title == "帆布托特"
            assert variant.body == SAFE_BODY
            assert variant.storyboard is None
            roles = list(
                fresh.scalars(
                    select(VariantAsset.role).where(VariantAsset.variant_id == variant.id)
                ).all()
            )
            assert "final_video" not in roles
        finally:
            fresh.close()
    finally:
        db.rollback()
        cleanup(db, user)


        db.rollback()
        cleanup(db, user)


def test_object_upload_spans_lease_and_orphan_is_dropped(client, db, monkeypatch):
    """对象存储上传本身跨过多个租期；失去租约后不提交资产关联，并删掉这次的孤立对象。"""
    user, product, fact, first, _second = _seed(db)
    owned = db.get(ImageAsset, product.primary_asset_id)
    blob: dict[str, bytes] = {owned.object_key: PRODUCT_PHOTO.read_bytes()}
    puts: list[str] = []
    delay = {"seconds": 0.0}
    seen = {"tx": None}

    def put_bytes(key, data, content_type):
        del content_type
        seen["tx"] = db.in_transaction()
        time.sleep(delay["seconds"])
        blob[key] = data
        puts.append(key)

    monkeypatch.setattr(storage, "put_bytes", put_bytes)
    monkeypatch.setattr(storage, "get_bytes", lambda key: blob.get(key, b""))
    monkeypatch.setattr(storage, "object_exists", lambda key: key in blob)
    monkeypatch.setattr(storage, "delete_object", lambda key: blob.pop(key, None))
    monkeypatch.setattr("app.pipeline_worker.produce_copy", lambda **_k: _content())
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    monkeypatch.setattr("app.pipeline_worker.lease_seconds", 120)
    monkeypatch.setattr("app.pipeline_worker.heartbeat_interval", 20)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "upload-lease"})
        for _ in range(3):
            claimed = claim_pending(db)
            assert claimed is not None
            process_step(db, claimed.step_id, claimed.token)

        delay["seconds"] = 1.6
        monkeypatch.setattr("app.pipeline_worker.lease_seconds", 0.9)
        monkeypatch.setattr("app.pipeline_worker.heartbeat_interval", 0.2)
        image = claim_pending(db)
        assert image is not None
        beats, expired = _run_with_scanner(db, image.step_id, image.token)
        assert seen["tx"] is False
        assert image.step_id not in expired
        assert puts, "上传没有发生"
        assert len(set(beats)) >= 3
        assert (max(beats) - min(beats)).total_seconds() > 0.9
        cover_key = puts[-1]
        assert cover_key in blob
        detail = client.get(f"/api/campaigns/{campaign_id}").json()
        by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
        assert by_key["image:douyin"]["status"] == "succeeded"
        assert by_key["image:douyin"]["output"]["asset_id"]

        def fast_clip(_source, dest, **_kwargs):
            Path(dest).write_bytes(b"orphan-video-bytes")
            return {
                "width": 720,
                "height": 1280,
                "qc": "needs_review",
                "qc_reason": "late",
                "qc_issue": None,
                "unplaced": "",
                "lines": ["迟到画面"],
                "duration": 18,
                "tool": "ffprobe",
                "codec": "h264",
                "audio_codec": "aac",
                "native_model_video": False,
                "audio_owned": True,
            }

        delay["seconds"] = 1.1
        monkeypatch.setattr("app.pipeline_worker.lease_seconds", 0.35)
        monkeypatch.setattr("app.pipeline_worker.heartbeat_interval", 0.5)
        monkeypatch.setattr("app.pipeline_worker.render_story_clip", fast_clip)
        video = claim_pending(db)
        assert video is not None
        _beats, expired = _run_with_scanner(db, video.step_id, video.token)
        assert len(puts) >= 2
        late_key = puts[-1]
        assert late_key != cover_key
        assert video.step_id in expired
        assert late_key not in blob
        assert cover_key in blob
        assert owned.object_key in blob
        fresh = SessionLocal()
        try:
            row = fresh.execute(
                text("SELECT status, error_code, output::text FROM pipeline_steps WHERE id = :id"),
                {"id": video.step_id},
            ).one()
            assert row.status == "unknown"
            assert row.error_code == "submit_unacked"
            assert "迟到画面" not in (row.output or "")
            assert fresh.scalar(select(ImageAsset.id).where(ImageAsset.object_key == late_key)) is None
            cover = fresh.scalar(select(ImageAsset).where(ImageAsset.object_key == cover_key))
            assert cover is not None
            link = fresh.scalar(select(VariantAsset).where(VariantAsset.asset_id == cover.id))
            assert link is not None and link.role == "cover"
            variant = fresh.scalar(
                select(ContentVariant).where(
                    ContentVariant.campaign_id == campaign_id,
                    ContentVariant.platform == "douyin",
                )
            )
            assert variant.title == "帆布托特"
            assert variant.body == SAFE_BODY
            roles = list(fresh.scalars(select(VariantAsset.role).where(VariantAsset.variant_id == variant.id)).all())
            assert "final_video" not in roles
        finally:
            fresh.close()
    finally:
        db.rollback()
        cleanup(db, user)


def test_internal_wording_is_not_rewritten_without_budget(client, db, monkeypatch):
    calls = {"n": 0}

    def bad(**_k):
        calls["n"] += 1
        return _content(REJECTED_BODY)

    monkeypatch.setattr("app.pipeline_worker.produce_copy", bad)
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={
                "product_id": product.id,
                "fact_version_id": fact.id,
                "source_item_ids": [first.id],
                "generation_budget": 1,
            },
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "qc-budget"})
        tick(db)
        tick(db)
        detail = client.get(f"/api/campaigns/{campaign_id}").json()
        by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
        assert calls["n"] == 1
        assert by_key["copy:douyin"]["status"] == "failed"
        assert by_key["copy:douyin"]["error_code"] == "internal_wording"
        assert by_key["image:douyin"]["status"] == "skipped"
        drafts = by_key["copy:douyin"]["output"]["drafts"]
        assert drafts[0]["body"] == REJECTED_BODY
        assert drafts[0]["issues"][0]["original"] == REJECTED_BODY
        assert by_key["copy:douyin"]["output"]["action"] == "human_edit"
        variant = next(item for item in detail["variants"] if item["platform"] == "douyin")
        assert variant["body"] is None
        run = db.get(CampaignRun, by_key["copy:douyin"]["id"] and detail["run"]["id"])
        assert run.budget_reserved == 1
    finally:
        cleanup(db, user)


def test_one_rewrite_uses_budget_and_keeps_the_rejected_draft(client, db, monkeypatch):
    calls = {"n": 0}

    def draft(**_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _content(REJECTED_BODY)
        return _content()

    monkeypatch.setattr("app.pipeline_worker.produce_copy", draft)
    monkeypatch.setattr("app.pipeline_worker.asset_data_url", lambda *_a, **_k: "data:image/png;base64,aa")
    user, product, fact, first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post(
            "/api/campaigns",
            json={
                "product_id": product.id,
                "fact_version_id": fact.id,
                "source_item_ids": [first.id],
                "generation_budget": 2,
            },
        )
        campaign_id = created.json()["id"]
        client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "qc-rewrite"})
        tick(db)
        tick(db)
        detail = client.get(f"/api/campaigns/{campaign_id}").json()
        by_key = {step["step_key"]: step for step in detail["run"]["steps"]}
        assert calls["n"] == 2
        assert by_key["copy:douyin"]["status"] == "succeeded"
        assert by_key["copy:douyin"]["output"]["rejected_drafts"][0]["body"] == REJECTED_BODY
        variant = next(item for item in detail["variants"] if item["platform"] == "douyin")
        assert variant["body"] == SAFE_BODY
        run = db.get(CampaignRun, detail["run"]["id"])
        assert run.budget_reserved == 2
    finally:
        cleanup(db, user)


def test_alembic_version_on_app_db(db):
    version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == "0014_operation_plans"
