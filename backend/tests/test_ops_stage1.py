"""运营阶段一的可选来源、单平台、总览与并发启动。"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Event, Thread

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.campaign import CampaignRun, ContentVariant, PipelineStep, VariantAsset, VariantReview
from app.models.publish import PublishJob
from app.pipeline_worker import claim_pending, process_step
from app.services.campaign_service import start_campaign
from tests.conftest import as_user, cleanup, make_asset, make_user
from tests.test_campaign_db import _seed


@pytest.mark.parametrize(
    ("platform", "step_keys"),
    [
        ("xiaohongshu", ["brief", "copy:xiaohongshu", "cards:xiaohongshu"]),
        ("douyin", ["brief", "copy:douyin", "image:douyin", "video:douyin"]),
    ],
)
def test_create_without_source_selects_only_requested_platform(client, db, platform, step_keys):
    user, product, fact, _first, _second = _seed(db)
    try:
        as_user(user)
        response = client.post("/api/campaigns", json={
            "product_id": product.id,
            "fact_version_id": fact.id,
            "source_item_ids": [],
            "target_platforms": [platform],
            "generation_requirements": "写通勤场景，不增加未确认的功能",
            "generation_budget": 2,
        })
        assert response.status_code == 200, response.text
        created = response.json()
        assert created["selected_source_item_ids"] == []
        assert created["target_platforms"] == [platform]
        assert created["brief"]["generation_requirements"] == "写通勤场景，不增加未确认的功能"
        assert [item["platform"] for item in created["variants"]] == [platform]
        assert [step["step_key"] for step in created["run"]["steps"]] == step_keys
        assert [item["platform"] for item in client.get(f"/api/campaigns/{created['id']}/review").json()["platforms"]] == [platform]
        assert [item["platform"] for item in client.get(f"/api/campaigns/{created['id']}/publish").json()["platforms"]] == [platform]
        for invalid in (["douyin", "douyin"], ["other"], []):
            rejected = client.post("/api/campaigns", json={
                "product_id": product.id, "fact_version_id": fact.id,
                "source_item_ids": [], "target_platforms": invalid,
            })
            assert rejected.status_code == 422
    finally:
        cleanup(db, user)


def test_single_platform_fact_fork_and_redo_remain_on_selected_platform(client, db):
    user, product, fact, _first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post("/api/campaigns", json={
            "product_id": product.id, "fact_version_id": fact.id,
            "target_platforms": ["xiaohongshu"],
        }).json()
        fork = client.post(f"/api/campaigns/{created['id']}/facts", json={
            "expected_fact_version": 1,
            "facts": {"material": "帆布", "waterproof": "needs_confirmation", "color": "棕色"},
        })
        assert fork.status_code == 200, fork.text
        review = client.get(f"/api/campaigns/{created['id']}/review").json()
        assert [item["platform"] for item in review["platforms"]] == ["xiaohongshu"]
        version = review["platforms"][0]["current"]["version"]
        assert version == 2
        redo = client.post(f"/api/campaigns/{created['id']}/variants/xiaohongshu/redo", json={
            "expected_version": version, "step_keys": ["cards:xiaohongshu"],
        })
        assert redo.status_code == 200, redo.text
        detail = client.get(f"/api/campaigns/{created['id']}").json()
        assert [item["platform"] for item in detail["variants"]] == [
            "xiaohongshu", "xiaohongshu", "xiaohongshu",
        ]
    finally:
        cleanup(db, user)


def test_recent_source_runs_are_owned_and_keep_provenance(client, db):
    user, product, fact, first, _second = _seed(db)
    other, _p2, _f2, _s1, _s2 = _seed(db)
    try:
        as_user(user)
        response = client.get("/api/collections/runs")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == first.run_id
        assert item["provider"] == "fixture"
        assert item["sort_metric"] == "sample_order"
        assert item["actual_count"] == 2
        assert item["started_at"]
    finally:
        cleanup(db, user, other)


def test_admin_cannot_mix_another_owners_source_into_product_campaign(client, db):
    source_owner, _source_product, _source_fact, foreign_source, _unused = _seed(db)
    product_owner, product, fact, own_source, _unused2 = _seed(db)
    admin = make_user(db, role="admin")
    try:
        as_user(admin)
        mixed = client.post("/api/campaigns", json={
            "product_id": product.id,
            "fact_version_id": fact.id,
            "source_item_ids": [foreign_source.id],
            "target_platforms": ["douyin"],
        })
        assert mixed.status_code == 404
        same_owner = client.post("/api/campaigns", json={
            "product_id": product.id,
            "fact_version_id": fact.id,
            "source_item_ids": [own_source.id],
            "target_platforms": ["douyin"],
        })
        assert same_owner.status_code == 200, same_owner.text
    finally:
        cleanup(db, source_owner, product_owner, admin)


def test_old_start_session_cannot_overwrite_committed_start(db):
    user, product, fact, _first, _second = _seed(db)
    try:
        from app.services.campaign_service import create_campaign

        campaign = create_campaign(
            db, user, product_id=product.id, fact_version_id=fact.id,
            source_item_ids=[], target_platforms=["douyin"],
        )
        campaign_id = campaign.id
        ready = Event()
        release = Event()
        seen: list[tuple[int, datetime | None, str | None]] = []

        def old_session() -> None:
            with SessionLocal() as session:
                stale = session.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign_id))
                assert stale is not None and stale.started_at is None
                ready.set()
                assert release.wait(10)
                run = start_campaign(session, user, campaign_id, idempotency_key="older")
                seen.append((run.id, run.started_at, run.idempotency_key))

        thread = Thread(target=old_session)
        thread.start()
        assert ready.wait(10)
        with SessionLocal() as newer:
            committed = start_campaign(newer, user, campaign_id, idempotency_key="newer")
            started_at = committed.started_at
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert seen == [(committed.id, started_at, "newer")]
        db.expire_all()
        run = db.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign_id))
        assert run is not None and run.idempotency_key == "newer"
    finally:
        cleanup(db, user)


def test_one_platform_failure_does_not_hide_other_approved_content(client, db):
    user, product, fact, _first, _second = _seed(db)
    try:
        as_user(user)
        response = client.post("/api/campaigns", json={
            "product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [],
            "target_platforms": ["douyin", "xiaohongshu"],
        })
        assert response.status_code == 200
        campaign_id = response.json()["id"]
        run = db.scalar(select(CampaignRun).where(CampaignRun.campaign_id == campaign_id))
        assert run is not None
        run.started_at = datetime.now(timezone.utc)
        rows = list(db.scalars(select(PipelineStep).where(PipelineStep.run_id == run.id)).all())
        for step in rows:
            step.status = "failed" if step.step_key == "copy:douyin" else "succeeded"
            if step.status == "failed":
                step.error_code = "copy_failed"
        xhs = db.scalar(select(ContentVariant).where(
            ContentVariant.campaign_id == campaign_id, ContentVariant.platform == "xiaohongshu"
        ))
        assert xhs is not None
        xhs.title = "帆布托特"
        xhs.body = "材质是帆布。"
        xhs.hashtags = ["箱包"]
        xhs.status = "approved"
        cover = make_asset(db, user)
        card = make_asset(db, user)
        db.add_all([
            VariantAsset(variant_id=xhs.id, asset_id=cover.id, role="cover", position=0),
            VariantAsset(variant_id=xhs.id, asset_id=card.id, role="card", position=1),
        ])
        db.flush()
        db.add(VariantReview(
            variant_id=xhs.id, version=1, reviewer_id=user.id, decision="approved",
            reviewed_at=datetime.now(timezone.utc),
            copy_snapshot={"version": 1, "title": xhs.title, "body": xhs.body, "hashtags": ["箱包"]},
            asset_order=[{"asset_id": cover.id, "role": "cover", "position": 0},
                         {"asset_id": card.id, "role": "card", "position": 1}],
            fact_version_id=fact.id, fact_snapshot=dict(fact.facts or {}), qc_snapshot={},
        ))
        db.commit()
        overview = client.get("/api/campaigns/overview")
        assert overview.status_code == 200, overview.text
        items = {item["platform"]: item for item in overview.json()["items"] if item["campaign_id"] == campaign_id}
        assert items["douyin"]["bucket"] == "pending_generation"
        assert items["xiaohongshu"]["bucket"] == "approved_ready"
        assert overview.json()["counts"]["approved_ready"] >= 1
        filtered = client.get("/api/campaigns/overview?bucket=approved_ready")
        assert [item["platform"] for item in filtered.json()["items"] if item["campaign_id"] == campaign_id] == ["xiaohongshu"]
        review = db.scalar(select(VariantReview).where(VariantReview.variant_id == xhs.id))
        assert review is not None
        job = PublishJob(
            owner_id=user.id, campaign_id=campaign_id, platform="xiaohongshu",
            variant_id=xhs.id, version=1, review_id=review.id,
            scheduled_at=datetime.now(timezone.utc), status="failed",
            readiness="approved_ready_to_publish", copy_snapshot={}, asset_order=[],
            missing_requirements=[], result={},
        )
        db.add(job)
        db.commit()
        exception = client.get("/api/campaigns/overview").json()
        item = next(item for item in exception["items"] if item["campaign_id"] == campaign_id and item["platform"] == "xiaohongshu")
        assert item["bucket"] == "publication_exception"
        assert item["publish_job_id"] == job.id
        # 旧版明确失败不应盖住新版待审核状态。
        db.add(ContentVariant(
            campaign_id=campaign_id, platform="xiaohongshu", content_type="note", version=2,
            status="needs_review", hashtags=[], fact_version_id=fact.id,
        ))
        db.commit()
        refreshed = client.get("/api/campaigns/overview").json()
        item = next(item for item in refreshed["items"] if item["campaign_id"] == campaign_id and item["platform"] == "xiaohongshu")
        assert item["bucket"] == "needs_review"
    finally:
        cleanup(db, user)


def test_unknown_generation_rejects_redo_and_fact_requeue(client, db):
    user, product, fact, _first, _second = _seed(db)
    try:
        as_user(user)
        created = client.post("/api/campaigns", json={
            "product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [],
            "target_platforms": ["douyin"],
        }).json()
        step = db.scalar(select(PipelineStep).where(
            PipelineStep.run_id == created["run"]["id"], PipelineStep.step_key == "copy:douyin"
        ))
        assert step is not None
        step.status = "unknown"
        step.error_code = "submit_unacked"
        db.commit()
        redo = client.post(f"/api/campaigns/{created['id']}/variants/douyin/redo", json={
            "expected_version": 1, "step_keys": ["copy:douyin"],
        })
        assert redo.status_code == 422
        assert redo.json()["detail"]["code"] == "result_unknown"
        fork = client.post(f"/api/campaigns/{created['id']}/facts", json={
            "expected_fact_version": 1, "facts": dict(fact.facts or {}),
        })
        assert fork.status_code == 422
        assert fork.json()["detail"]["code"] == "result_unknown"
        assert db.scalar(select(ContentVariant).where(
            ContentVariant.campaign_id == created["id"], ContentVariant.version == 2
        )) is None
    finally:
        cleanup(db, user)


def test_missing_model_config_leaves_copy_pending_without_external_call(client, db, monkeypatch):
    user, product, fact, _first, _second = _seed(db)
    try:
        monkeypatch.setattr(settings, "ARK_API_KEY", "")
        as_user(user)
        created = client.post("/api/campaigns", json={
            "product_id": product.id, "fact_version_id": fact.id,
            "source_item_ids": [], "target_platforms": ["douyin"],
        }).json()
        campaign_id = created["id"]
        started = client.post(f"/api/campaigns/{campaign_id}/start")
        assert started.status_code == 200
        assert started.json()["generation_connection"] == "pending_connection"
        brief = claim_pending(db)
        assert brief is not None
        process_step(db, brief.step_id, brief.token)
        assert claim_pending(db) is None
        current = client.get(f"/api/campaigns/{campaign_id}").json()
        copy = next(step for step in current["run"]["steps"] if step["step_key"] == "copy:douyin")
        assert copy["status"] == "pending"
        assert current["run"]["budget_reserved"] == 0
        overview = client.get("/api/campaigns/overview").json()
        item = next(item for item in overview["items"] if item["campaign_id"] == campaign_id)
        assert item["bucket"] == "pending_generation"
        assert item["blockers"][0]["code"] == "model_pending_connection"
    finally:
        cleanup(db, user)
