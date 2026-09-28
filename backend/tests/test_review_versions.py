"""审核绑定变体版本。旧批准、旧写回和另一侧结果都不能串。"""

import copy
import threading

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import ImageAsset
from app.models.campaign import Campaign, CampaignRun, ContentVariant, PipelineStep, VariantAsset, VariantReview
from app.models.copywriting import CopywritingOperation
from app.models.product import ProductFactVersion
from app.pipeline_worker import _write_variant, claim_pending, tick
from app.schemas.copywriting import GeneratedContent
from app.services import storage
from app.services.variant_review import (
    ReviewBlocked,
    VersionConflict,
    approve_variant,
    edit_variant,
    fork_facts,
    redo_variant,
    reject_variant,
)
from tests.conftest import as_user, cleanup, make_user
from tests.test_campaign_db import PRODUCT_PHOTO, _seed


def _open(client, db):
    user, product, fact, first, _second = _seed(db)
    as_user(user)
    created = client.post(
        "/api/campaigns",
        json={"product_id": product.id, "fact_version_id": fact.id, "source_item_ids": [first.id]},
    )
    campaign_id = created.json()["id"]
    client.post(f"/api/campaigns/{campaign_id}/start", headers={"Idempotency-Key": "review"})
    detail = client.get(f"/api/campaigns/{campaign_id}").json()
    return user, product, fact, campaign_id, detail


def _step(db, run_id: int, key: str) -> PipelineStep:
    return db.scalar(
        select(PipelineStep).where(PipelineStep.run_id == run_id, PipelineStep.step_key == key, PipelineStep.version == 1)
    )


class _Actor:
    def __init__(self, user) -> None:
        self.id = user.id
        self.role = user.role


@pytest.fixture(autouse=True)
def storage_reads_as_present(monkeypatch, request):
    if request.node.get_closest_marker("real_storage"):
        return
    monkeypatch.setattr("app.services.storage.object_exists", lambda _key: True)
    monkeypatch.setattr("app.services.storage.get_bytes", lambda _key: b"stored-bytes")


def _race(calls):
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def run(index, fn):
        session = SessionLocal()
        try:
            barrier.wait(20)
            results[index] = fn(session)
        except Exception as exc:
            session.rollback()
            results[index] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=run, args=(index, fn)) for index, fn in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
        assert not thread.is_alive()
    return results


def test_cross_account_cannot_read_review(client, db):
    user, _product, _fact, campaign_id, _detail = _open(client, db)
    other = make_user(db)
    try:
        as_user(other)
        denied = client.get(f"/api/campaigns/{campaign_id}/review")
        assert denied.status_code == 404
        edit = client.patch(
            f"/api/campaigns/{campaign_id}/variants/douyin",
            json={"expected_version": 1, "title": "帆布托特", "body": "材质是帆布。"},
        )
        assert edit.status_code == 404
    finally:
        cleanup(db, user, other)


def test_old_approval_does_not_cover_new_version(client, db):
    user, _product, _fact, campaign_id, detail = _open(client, db)
    try:
        _ready_platform(db, detail, "douyin")
        _ready_platform(db, detail, "xiaohongshu")
        db.commit()
        approved = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/approve",
            json={"expected_version": 1, "comment": "这一版可以"},
        )
        assert approved.status_code == 200, approved.text
        edited = client.patch(
            f"/api/campaigns/{campaign_id}/variants/douyin",
            headers={"If-Match": "1"},
            json={"title": "帆布托特", "body": "材质是帆布。"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["version"] == 2
        assert edited.json()["status"] == "draft"
        new_id = edited.json()["id"]
        assert db.scalars(select(VariantAsset).where(VariantAsset.variant_id == new_id)).all() == []
        run_id = detail["run"]["id"]
        human = db.scalar(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == "copy:douyin",
                PipelineStep.version == 2,
            )
        )
        assert human.status == "succeeded"
        assert human.output["source"] == "human_edit"
        assert human.copywriting_operation_id is None
        assert human.variant_id == new_id
        old_copy = _step(db, run_id, "copy:douyin")
        assert old_copy.status == "succeeded"
        assert old_copy.id != human.id
        for key in ("image:douyin", "video:douyin"):
            step = db.scalar(
                select(PipelineStep).where(
                    PipelineStep.run_id == run_id,
                    PipelineStep.step_key == key,
                    PipelineStep.version == 2,
                )
            )
            assert step is not None and step.status == "pending"
            assert step.variant_id == new_id
            assert step.depends_on == (["copy:douyin"] if key == "image:douyin" else ["image:douyin"])
            assert step.input_hash == f"human:{human.id}"
        payload = client.get(f"/api/campaigns/{campaign_id}/review").json()
        douyin = next(item for item in payload["platforms"] if item["platform"] == "douyin")
        assert douyin["current"]["version"] == 2
        assert douyin["current"]["status"] == "draft"
        assert any(item["decision"] == "approved" and item["variant_id"] != douyin["current"]["id"] for item in douyin["reviews"])
        xhs = next(item for item in payload["platforms"] if item["platform"] == "xiaohongshu")
        assert xhs["current"]["status"] != "approved"
        again = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/approve",
            json={"expected_version": 2},
        )
        assert again.status_code == 422
        assert again.json()["detail"]["code"] in {"generation_incomplete", "assets_missing"}
    finally:
        cleanup(db, user)


def test_concurrent_edit_returns_409(client, db):
    user, _product, _fact, campaign_id, _detail = _open(client, db)
    try:
        first = client.patch(
            f"/api/campaigns/{campaign_id}/variants/xiaohongshu",
            json={"expected_version": 1, "title": "帆布托特", "body": "材质是帆布。"},
        )
        assert first.status_code == 200, first.text
        second = client.patch(
            f"/api/campaigns/{campaign_id}/variants/xiaohongshu",
            headers={"If-Match": "1"},
            json={"expected_version": 1, "body": "材质是帆布。"},
        )
        assert second.status_code == 409
    finally:
        cleanup(db, user)


def test_late_write_stays_on_the_bound_version(client, db):
    user, _product, _fact, campaign_id, detail = _open(client, db)
    try:
        edited = client.patch(
            f"/api/campaigns/{campaign_id}/variants/douyin",
            json={"expected_version": 1, "title": "帆布托特", "body": "材质是帆布。"},
        )
        assert edited.status_code == 200
        run_id = detail["run"]["id"]
        old = _step(db, run_id, "copy:douyin")
        new = db.scalar(
            select(ContentVariant).where(
                ContentVariant.campaign_id == campaign_id,
                ContentVariant.platform == "douyin",
                ContentVariant.version == 2,
            )
        )
        new.title = "新版本标题"
        db.commit()
        _write_variant(db, old, GeneratedContent(title="迟到结果", body="材质是帆布。", hashtags=["箱包"]))
        db.commit()
        db.refresh(old)
        bound = db.get(ContentVariant, old.variant_id)
        fresh = db.get(ContentVariant, new.id)
        assert bound.version == 1
        assert bound.title == "迟到结果"
        assert fresh.title == "新版本标题"
    finally:
        cleanup(db, user)


def test_qc_blocks_approval_without_touching_the_other_side(client, db):
    user, _product, _fact, campaign_id, detail = _open(client, db)
    try:
        _ready_platform(db, detail, "douyin")
        _ready_platform(db, detail, "xiaohongshu")
        cards = _step(db, detail["run"]["id"], "cards:xiaohongshu")
        cards.output = {"qc_issue": "content_insufficient", "review_notes": ["内部备注留在这里"]}
        cards.error_code = "content_insufficient"
        db.commit()
        denied = client.post(
            f"/api/campaigns/{campaign_id}/variants/xiaohongshu/approve",
            json={"expected_version": 1},
        )
        assert denied.status_code == 422
        assert denied.json()["detail"]["code"] == "content_insufficient"
        allowed = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/approve",
            json={"expected_version": 1, "comment": "抖音这一侧可以"},
        )
        assert allowed.status_code == 200, allowed.text
        payload = client.get(f"/api/campaigns/{campaign_id}/review").json()
        xhs = next(item for item in payload["platforms"] if item["platform"] == "xiaohongshu")
        douyin = next(item for item in payload["platforms"] if item["platform"] == "douyin")
        assert xhs["current"]["status"] != "approved"
        assert douyin["current"]["status"] == "approved"
    finally:
        cleanup(db, user)


def test_partial_redo_keeps_reusable_steps(client, db):
    user, _product, _fact, campaign_id, detail = _open(client, db)
    try:
        _ready_platform(db, detail, "douyin")
        _ready_platform(db, detail, "xiaohongshu")
        cards = _step(db, detail["run"]["id"], "cards:xiaohongshu")
        cards.status = "failed"
        cards.error_code = "content_insufficient"
        brief = _step(db, detail["run"]["id"], "brief")
        brief.status = "succeeded"
        db.commit()
        redone = client.post(
            f"/api/campaigns/{campaign_id}/variants/xiaohongshu/redo",
            json={"expected_version": 1, "step_keys": ["cards:xiaohongshu"], "comment": "只重做组图"},
        )
        assert redone.status_code == 200, redone.text
        assert redone.json()["version"] == 2
        kept = db.get(ContentVariant, redone.json()["id"])
        assert kept.title == "帆布托特"
        assert kept.body == "材质是帆布。"
        assert kept.status == "draft"
        run_id = detail["run"]["id"]
        video = _step(db, run_id, "video:douyin")
        assert video.status == "succeeded"
        assert video.version == 1
        new_video = db.scalar(
            select(PipelineStep).where(PipelineStep.run_id == run_id, PipelineStep.step_key == "video:douyin", PipelineStep.version == 2)
        )
        assert new_video is None
        new_cards = db.scalar(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == "cards:xiaohongshu",
                PipelineStep.version == 2,
            )
        )
        assert new_cards.status == "pending"
        assert new_cards.variant_id == redone.json()["id"]
        assert (
            db.scalar(
                select(PipelineStep).where(
                    PipelineStep.run_id == run_id,
                    PipelineStep.step_key == "copy:xiaohongshu",
                    PipelineStep.version == 2,
                )
            )
            is None
        )
        claim = claim_pending(db)
        assert claim is not None
        claimed = db.get(PipelineStep, claim.step_id)
        assert claimed.step_key == "cards:xiaohongshu"
        assert claimed.version == 2
        assert claimed.variant_id == new_cards.variant_id
    finally:
        cleanup(db, user)


def test_video_redo_reuses_cover_and_leaves_the_other_platform(client, db):
    user, _product, _fact, campaign_id, detail = _open(client, db)
    try:
        _ready_platform(db, detail, "douyin")
        _ready_platform(db, detail, "xiaohongshu")
        db.commit()
        redone = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/redo",
            json={"expected_version": 1, "step_keys": ["video:douyin"], "comment": "只重做成片"},
        )
        assert redone.status_code == 200, redone.text
        created = db.get(ContentVariant, redone.json()["id"])
        roles = {
            row.role
            for row in db.scalars(select(VariantAsset).where(VariantAsset.variant_id == created.id)).all()
        }
        assert "cover" in roles
        assert "final_video" not in roles
        run_id = detail["run"]["id"]
        video = db.scalar(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == "video:douyin",
                PipelineStep.version == 2,
            )
        )
        image = db.scalar(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == "image:douyin",
                PipelineStep.version == 2,
            )
        )
        cards = db.scalar(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == "cards:xiaohongshu",
                PipelineStep.version == 2,
            )
        )
        assert video.status == "pending" and video.variant_id == created.id
        assert image is None
        assert cards is None
        xhs = db.scalar(
            select(ContentVariant).where(
                ContentVariant.campaign_id == campaign_id,
                ContentVariant.platform == "xiaohongshu",
                ContentVariant.version == 1,
            )
        )
        assert xhs.status == "needs_review"
    finally:
        cleanup(db, user)


def test_review_record_keeps_snapshot_and_fact_fork_does_not_rewrite_old(client, db):
    user, _product, fact, campaign_id, detail = _open(client, db)
    try:
        _ready_platform(db, detail, "douyin")
        db.commit()
        approved = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/approve",
            json={"expected_version": 1, "comment": "留下快照"},
        )
        assert approved.status_code == 200, approved.text
        row = db.get(VariantReview, approved.json()["id"])
        assert row.reviewer_id == user.id
        assert row.reviewed_at is not None
        assert row.decision == "approved"
        assert row.version == 1
        assert row.comment == "留下快照"
        assert row.copy_snapshot["body"] == "材质是帆布。"
        assert row.asset_order
        assert row.fact_version_id == fact.id
        assert row.fact_snapshot["material"] == "帆布"
        old_facts = dict(fact.facts)
        forked = client.post(
            f"/api/campaigns/{campaign_id}/facts",
            json={"expected_fact_version": fact.version, "facts": {"material": "测试材质", "waterproof": "needs_confirmation"}},
        )
        assert forked.status_code == 200, forked.text
        db.refresh(fact)
        assert fact.facts == old_facts
        assert forked.json()["version"] == fact.version + 1
        old_variant = db.scalar(
            select(ContentVariant).where(
                ContentVariant.campaign_id == campaign_id,
                ContentVariant.platform == "douyin",
                ContentVariant.version == 1,
            )
        )
        assert old_variant.fact_version_id == fact.id
        assert old_variant.status == "approved"
        new_fact = db.get(ProductFactVersion, forked.json()["id"])
        assert new_fact.facts["material"] == "测试材质"
        assert fact.facts["material"] == "帆布"
        fresh = db.scalar(
            select(ContentVariant).where(
                ContentVariant.campaign_id == campaign_id,
                ContentVariant.platform == "douyin",
                ContentVariant.version == 2,
            )
        )
        assert fresh.status != "approved"
        assert fresh.fact_version_id == forked.json()["id"]
        assert fresh.title is None
    finally:
        cleanup(db, user)


def _ready_platform(db, detail, platform: str) -> None:
    run = db.get(CampaignRun, detail["run"]["id"])
    keys = ("copy:douyin", "image:douyin", "video:douyin") if platform == "douyin" else (
        "copy:xiaohongshu",
        "cards:xiaohongshu",
    )
    variant = db.scalar(
        select(ContentVariant).where(
            ContentVariant.campaign_id == detail["id"],
            ContentVariant.platform == platform,
            ContentVariant.version == 1,
        )
    )
    variant.title = "帆布托特"
    variant.body = "材质是帆布。"
    variant.hashtags = ["箱包"]
    variant.status = "needs_review"
    for key in keys:
        step = _step(db, run.id, key)
        step.status = "succeeded"
        step.output = {"qc_issue": None, "review_notes": ["防水未确认，只留在内部备注"], "lines": ["材质：帆布"]}
        step.error_code = None
    cover = ImageAsset(
        owner_id=run.campaign.owner_id if False else _owner(db, detail),
        bucket="aigc-images",
        object_key=f"tests/review/{variant.id}-cover.png",
        mime="image/png",
        size_bytes=12,
        width=8,
        height=8,
    )
    extra = ImageAsset(
        owner_id=_owner(db, detail),
        bucket="aigc-images",
        object_key=f"tests/review/{variant.id}-extra.png",
        mime="video/mp4" if platform == "douyin" else "image/png",
        size_bytes=12,
        width=8,
        height=8,
    )
    db.add(cover)
    db.add(extra)
    db.flush()
    db.add(VariantAsset(variant_id=variant.id, asset_id=cover.id, role="cover", position=0))
    db.add(
        VariantAsset(
            variant_id=variant.id,
            asset_id=extra.id,
            role="final_video" if platform == "douyin" else "card",
            position=1,
        )
    )


def _owner(db, detail) -> int:
    return db.get(Campaign, detail["id"]).owner_id


def _qualified():
    return {"title": "帆布托特", "body": "材质是帆布。", "hashtags": ["箱包"]}


def _versions(db, campaign_id: int, platform: str) -> list[ContentVariant]:
    db.rollback()
    db.expire_all()
    return list(
        db.scalars(
            select(ContentVariant)
            .where(ContentVariant.campaign_id == campaign_id, ContentVariant.platform == platform)
            .order_by(ContentVariant.version.asc())
        ).all()
    )


def test_two_sessions_cannot_both_edit_the_same_version(client, db):
    user, _product, _fact, campaign_id, _detail = _open(client, db)
    try:
        actor = _Actor(user)
        db.commit()
        payload = _qualified()

        def edit(session):
            row = edit_variant(session, actor, campaign_id, "xiaohongshu", expected_version=1, **payload)
            return {"version": row.version, "id": row.id, "status": row.status}

        results = _race([edit, edit])
        successes = [item for item in results if isinstance(item, dict)]
        conflicts = [item for item in results if isinstance(item, VersionConflict)]
        assert len(successes) == 1, results
        assert len(conflicts) == 1, results
        rows = _versions(db, campaign_id, "xiaohongshu")
        assert [row.version for row in rows] == [1, 2]
        assert rows[1].status != "approved"
        assert successes[0]["version"] == 2
    finally:
        cleanup(db, user)


def test_concurrent_approve_binds_the_locked_version(client, db):
    user, _product, _fact, campaign_id, detail = _open(client, db)
    try:
        _ready_platform(db, detail, "douyin")
        db.commit()
        actor = _Actor(user)
        original = db.scalar(
            select(ContentVariant).where(
                ContentVariant.campaign_id == campaign_id,
                ContentVariant.platform == "douyin",
                ContentVariant.version == 1,
            )
        )
        original_id = original.id
        db.commit()

        def approve(session):
            row = approve_variant(
                session, actor, campaign_id, "douyin", expected_version=1, comment="并发通过"
            )
            return {"version": row.version, "variant_id": row.variant_id}

        def edit(session):
            row = edit_variant(session, actor, campaign_id, "douyin", expected_version=1, **_qualified())
            return {"version": row.version, "id": row.id}

        results = _race([approve, edit])
        assert not any(isinstance(item, ReviewBlocked) for item in results), results
        rows = _versions(db, campaign_id, "douyin")
        assert [row.version for row in rows] == [1, 2]
        current = rows[-1]
        assert current.status != "approved"
        reviews = list(
            db.scalars(select(VariantReview).where(VariantReview.variant_id.in_([row.id for row in rows]))).all()
        )
        approved = [row for row in reviews if row.decision == "approved"]
        approve_result, edit_result = results
        if isinstance(approve_result, VersionConflict):
            assert isinstance(edit_result, dict)
            assert approved == []
            assert rows[0].status != "approved"
        else:
            assert isinstance(approve_result, dict), approve_result
            assert approve_result["version"] == 1
            assert approve_result["variant_id"] == original_id
            assert approved
            assert all(row.version == 1 and row.variant_id == original_id for row in approved)
            assert rows[0].status == "approved"
        assert all(row.variant_id != current.id for row in approved)
    finally:
        cleanup(db, user)


def test_concurrent_reject_does_not_reject_the_new_version(client, db):
    user, _product, _fact, campaign_id, _detail = _open(client, db)
    try:
        actor = _Actor(user)
        db.commit()
        original = db.scalar(
            select(ContentVariant).where(
                ContentVariant.campaign_id == campaign_id,
                ContentVariant.platform == "xiaohongshu",
                ContentVariant.version == 1,
            )
        )
        original_id = original.id
        db.commit()

        def reject(session):
            row = reject_variant(session, actor, campaign_id, "xiaohongshu", expected_version=1, comment="并发退回")
            return {"version": row.version, "variant_id": row.variant_id}

        def edit(session):
            row = edit_variant(session, actor, campaign_id, "xiaohongshu", expected_version=1, **_qualified())
            return {"version": row.version, "id": row.id}

        results = _race([reject, edit])
        reject_result, edit_result = results
        rows = _versions(db, campaign_id, "xiaohongshu")
        assert [row.version for row in rows] == [1, 2]
        reviews = list(db.scalars(select(VariantReview).where(VariantReview.variant_id.in_([row.id for row in rows]))).all())
        rejected = [row for row in reviews if row.decision == "rejected"]
        assert all(row.version == 1 and row.variant_id == original_id for row in rejected)
        assert rows[1].status != "rejected"
        if isinstance(reject_result, VersionConflict):
            assert isinstance(edit_result, dict)
            assert rejected == []
        else:
            assert isinstance(reject_result, dict), reject_result
            assert reject_result["version"] == 1
            assert reject_result["variant_id"] == original_id
            assert rows[0].status == "rejected"
    finally:
        cleanup(db, user)


def test_concurrent_redo_and_edit_only_one_new_version(client, db):
    user, _product, _fact, campaign_id, _detail = _open(client, db)
    try:
        actor = _Actor(user)
        db.commit()

        def redo(session):
            row = redo_variant(
                session,
                actor,
                campaign_id,
                "douyin",
                expected_version=1,
                step_keys=["video:douyin"],
                comment="并发重做",
            )
            return {"version": row.version, "id": row.id}

        def edit(session):
            row = edit_variant(session, actor, campaign_id, "douyin", expected_version=1, **_qualified())
            return {"version": row.version, "id": row.id}

        results = _race([redo, edit])
        successes = [item for item in results if isinstance(item, dict)]
        conflicts = [item for item in results if isinstance(item, VersionConflict)]
        assert len(successes) == 1, results
        assert len(conflicts) == 1, results
        rows = _versions(db, campaign_id, "douyin")
        assert [row.version for row in rows] == [1, 2]
        assert rows[1].status != "approved"
    finally:
        cleanup(db, user)


def test_two_sessions_fork_facts_once(client, db):
    user, _product, fact, campaign_id, _detail = _open(client, db)
    try:
        actor = _Actor(user)
        expected = fact.version
        old_facts = dict(fact.facts)
        product_id = fact.product_id
        fact_id = fact.id
        db.commit()

        def fork(session):
            row = fork_facts(
                session,
                actor,
                campaign_id,
                expected_fact_version=expected,
                facts={"material": "测试材质", "waterproof": "needs_confirmation"},
            )
            return {"version": row.version, "id": row.id}

        results = _race([fork, fork])
        successes = [item for item in results if isinstance(item, dict)]
        conflicts = [item for item in results if isinstance(item, VersionConflict)]
        assert len(successes) == 1, results
        assert len(conflicts) == 1, results
        assert successes[0]["version"] == expected + 1
        db.rollback()
        db.expire_all()
        fresh = db.get(ProductFactVersion, fact_id)
        assert fresh.facts == old_facts
        assert fresh.version == expected
        count = db.scalar(
            select(func.count()).select_from(ProductFactVersion).where(ProductFactVersion.product_id == product_id)
        )
        assert count == 2
        rows = _versions(db, campaign_id, "douyin")
        assert [row.version for row in rows] == [1, 2]
        assert rows[0].fact_version_id == fact_id
        assert rows[1].fact_version_id != fact_id
        assert rows[1].status != "approved"
    finally:
        cleanup(db, user)


def test_failed_copy_human_edit_generates_media_without_calling_the_model(client, db, monkeypatch):
    user, product, _fact, campaign_id, detail = _open(client, db)
    calls = {"n": 0}

    def refuse_copy(**_kwargs):
        calls["n"] += 1
        raise AssertionError("不应再调用文案模型")

    owned = db.get(ImageAsset, product.primary_asset_id)
    blob: dict[str, bytes] = {owned.object_key: PRODUCT_PHOTO.read_bytes()}

    def put_bytes(key, data, content_type):
        del content_type
        blob[key] = data

    monkeypatch.setattr(storage, "put_bytes", put_bytes)
    monkeypatch.setattr(storage, "get_bytes", lambda key: blob[key])
    monkeypatch.setattr(storage, "object_exists", lambda key: key in blob)
    monkeypatch.setattr(storage, "delete_object", lambda key: blob.pop(key, None))
    monkeypatch.setattr("app.pipeline_worker.produce_copy", refuse_copy)
    try:
        run_id = detail["run"]["id"]
        frozen = {"drafts": [{"title": "通勤托特", "body": "没有依据"}], "action": "human_edit", "frozen": "v1-failed"}
        for step in db.scalars(select(PipelineStep).where(PipelineStep.run_id == run_id)).all():
            if step.step_key == "copy:douyin":
                step.status = "failed"
                step.error_code = "ungrounded"
                step.output = dict(frozen)
            elif step.step_key in {"image:douyin", "video:douyin"}:
                step.status = "skipped"
                step.error_code = "blocked_by_upstream"
            else:
                step.status = "succeeded"
                step.error_code = None
        campaign = db.get(Campaign, campaign_id)
        campaign.status = "failed"
        db.commit()
        old = _step(db, run_id, "copy:douyin")
        old_id = old.id
        old_output = copy.deepcopy(dict(old.output))
        ops_before = db.scalar(
            select(func.count()).select_from(CopywritingOperation).where(CopywritingOperation.user_id == user.id)
        )
        edited = client.patch(
            f"/api/campaigns/{campaign_id}/variants/douyin",
            json={"expected_version": 1, **_qualified()},
        )
        assert edited.status_code == 200, edited.text
        new_id = edited.json()["id"]
        db.expire_all()
        human = db.scalar(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == "copy:douyin",
                PipelineStep.version == 2,
            )
        )
        assert human.status == "succeeded"
        assert human.output["source"] == "human_edit"
        assert human.copywriting_operation_id is None
        assert human.provider_request_id is None
        assert human.variant_id == new_id
        image = db.scalar(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == "image:douyin",
                PipelineStep.version == 2,
            )
        )
        video = db.scalar(
            select(PipelineStep).where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == "video:douyin",
                PipelineStep.version == 2,
            )
        )
        assert image.depends_on == ["copy:douyin"]
        assert video.depends_on == ["image:douyin"]
        assert image.input_hash == f"human:{human.id}"
        assert video.input_hash == f"human:{human.id}"
        parent = db.scalar(
            select(PipelineStep)
            .where(
                PipelineStep.run_id == run_id,
                PipelineStep.step_key == "copy:douyin",
                PipelineStep.version <= image.version,
            )
            .order_by(PipelineStep.version.desc())
        )
        assert parent.id == human.id
        assert db.get(Campaign, campaign_id).status == "generating"
        image_id = image.id
        video_id = video.id
        for _ in range(8):
            db.expire_all()
            tick(db)
            db.expire_all()
            image = db.get(PipelineStep, image_id)
            video = db.get(PipelineStep, video_id)
            if image.status == "succeeded" and video.status == "succeeded":
                break
        assert calls["n"] == 0
        assert image.status == "succeeded", image.error_code
        assert video.status == "succeeded", (video.status, video.error_code, video.output)
        assert image.variant_id == new_id
        assert video.variant_id == new_id
        kept = db.get(PipelineStep, old_id)
        assert kept.status == "failed"
        assert kept.error_code == "ungrounded"
        assert kept.output == old_output
        assert kept.id != human.id
        ops_after = db.scalar(
            select(func.count()).select_from(CopywritingOperation).where(CopywritingOperation.user_id == user.id)
        )
        assert ops_after == ops_before
        roles = {
            row.role
            for row in db.scalars(select(VariantAsset).where(VariantAsset.variant_id == new_id)).all()
        }
        assert roles == {"cover", "final_video"}
        payload = client.get(f"/api/campaigns/{campaign_id}/review").json()
        douyin = next(item for item in payload["platforms"] if item["platform"] == "douyin")
        assert douyin["current"]["version"] == 2
        assert douyin["blockers"] == []
        assert any(step["source"] == "human_edit" and step["step_key"] == "copy:douyin" for step in douyin["steps"])
        approved = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/approve",
            json={"expected_version": 2, "comment": "人工文案这一版可以"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["decision"] == "approved"
        assert approved.json()["variant_id"] == new_id
        review = db.get(VariantReview, approved.json()["id"])
        assert review.version == 2
        assert review.variant_id == new_id
    finally:
        cleanup(db, user)


def test_missing_or_unreadable_object_blocks_approval(client, db, monkeypatch):
    user, product, _fact, campaign_id, detail = _open(client, db)
    try:
        _ready_platform(db, detail, "douyin")
        db.commit()
        monkeypatch.setattr("app.services.storage.object_exists", lambda _key: False)
        missing = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/approve",
            json={"expected_version": 1},
        )
        assert missing.status_code == 422
        assert missing.json()["detail"]["code"] == "asset_unreadable"
        assert "封面在对象存储中不存在" in missing.json()["detail"]["message"]
        variant = db.scalar(
            select(ContentVariant).where(
                ContentVariant.campaign_id == campaign_id,
                ContentVariant.platform == "douyin",
                ContentVariant.version == 1,
            )
        )
        assert variant.status != "approved"

        def unreadable(_key):
            raise OSError("storage down")

        monkeypatch.setattr("app.services.storage.object_exists", lambda _key: True)
        monkeypatch.setattr("app.services.storage.get_bytes", unreadable)
        denied = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/approve",
            json={"expected_version": 1},
        )
        assert denied.status_code == 422
        assert denied.json()["detail"]["code"] == "asset_unreadable"
        assert "无法从对象存储读取" in denied.json()["detail"]["message"]

        monkeypatch.setattr("app.services.storage.get_bytes", lambda _key: b"")
        empty = client.post(
            f"/api/campaigns/{campaign_id}/variants/douyin/approve",
            json={"expected_version": 1},
        )
        assert empty.status_code == 422
        assert empty.json()["detail"]["code"] == "asset_unreadable"
        assert "空文件" in empty.json()["detail"]["message"]
        db.expire_all()
        assert db.get(ContentVariant, variant.id).status != "approved"

        monkeypatch.setattr("app.services.storage.get_bytes", unreadable)
        preview = client.get(f"/api/assets/{product.primary_asset_id}/file")
        assert preview.status_code == 404
        assert preview.json()["detail"] == "对象存储里没有这个文件或无法读取"
    finally:
        cleanup(db, user)


@pytest.mark.real_storage
def test_configured_object_storage_roundtrip_or_unaccepted(client, db):
    """审核文件接口走当前配置的对象存储。连不上就明确未验收，不用内存替身冒充。"""
    from app.core.config import settings
    from app.core.minio_client import ensure_bucket

    user = make_user(db)
    key = f"acceptance/review-read-{user.id}.jpg"
    payload = PRODUCT_PHOTO.read_bytes()
    try:
        try:
            ensure_bucket()
            storage.put_bytes(key, payload, "image/jpeg")
        except Exception as exc:
            pytest.skip(f"未验收：当前环境无法提供真实对象存储（{type(exc).__name__}: {exc}）")
        assert storage.object_exists(key) is True
        assert storage.get_bytes(key) == payload
        asset = ImageAsset(
            owner_id=user.id,
            bucket=settings.MINIO_BUCKET,
            object_key=key,
            mime="image/jpeg",
            size_bytes=len(payload),
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        as_user(user)
        resp = client.get(f"/api/assets/{asset.id}/file")
        assert resp.status_code == 200, resp.text
        assert resp.content == payload
        disposition = resp.headers["content-disposition"]
        assert disposition.startswith("inline;")
        assert f'filename="yingpeng-{asset.id}.jpg"' in disposition
    finally:
        try:
            storage.delete_object(key)
        except Exception:
            pass
        cleanup(db, user)
