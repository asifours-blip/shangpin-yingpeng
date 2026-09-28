"""编排：双领取、崩溃不重交、事实约束、失败不丢掉已成功的文案。"""

from datetime import datetime, timezone

from app.services.campaign_pipeline import (
    begin_submit,
    blocked_keys,
    campaign_status,
    claim_one,
    crash_before_ack,
    locked_fact_snapshot,
    mark_failed,
    mark_succeeded,
    new_recipe,
    note_provider_request,
    plan_from_snapshot,
    propose_topics,
    recoverable,
    text_asserts_unconfirmed,
)


def _now():
    return datetime(2026, 9, 26, tzinfo=timezone.utc)


def test_two_workers_cannot_claim_the_same_step():
    steps = new_recipe()
    held: set[tuple[str, str, int]] = set()
    first = claim_one(steps, now=_now(), held=held)
    second = claim_one(steps, now=_now(), held=held)
    assert first is not None and first.step_key == "brief"
    assert second is None
    assert first.attempt == 1


def test_copies_wait_for_brief_then_split():
    steps = new_recipe()
    held: set[tuple[str, str, int]] = set()
    brief = claim_one(steps, now=_now(), held=held)
    assert brief is not None
    mark_succeeded(brief, {"topics": 1})
    held.discard(brief.identity)
    left = claim_one(steps, now=_now(), held=held)
    right = claim_one(steps, now=_now(), held=held)
    assert left is not None and right is not None
    assert {left.step_key, right.step_key} == {"copy:douyin", "copy:xiaohongshu"}


def test_crash_without_provider_id_becomes_unknown_and_is_not_reclaimed():
    steps = new_recipe()
    held: set[tuple[str, str, int]] = set()
    step = claim_one(steps, now=_now(), held=held)
    assert step is not None
    begin_submit(step)
    assert crash_before_ack(step) == "unknown"
    held.discard(step.identity)
    again = claim_one(steps, now=_now(), held=held)
    assert again is None
    assert step.attempt == 1


def test_known_provider_id_is_reconciled_not_resubmitted():
    steps = new_recipe()
    held: set[tuple[str, str, int]] = set()
    step = claim_one(steps, now=_now(), held=held)
    assert step is not None
    begin_submit(step)
    note_provider_request(step, "ark-task-9")
    assert crash_before_ack(step) == "reconcile"
    assert step.status == "running"
    assert [item.provider_request_id for item in recoverable(steps)] == ["ark-task-9"]
    held.discard(step.identity)
    assert claim_one(steps, now=_now(), held=held) is None


def test_image_failure_blocks_video_but_keeps_copy():
    steps = new_recipe()
    by_key = {step.step_key: step for step in steps}
    mark_succeeded(by_key["brief"], {})
    mark_succeeded(by_key["copy:douyin"], {"title": "通勤托特"})
    mark_succeeded(by_key["copy:xiaohongshu"], {"title": "另一套笔记"})
    mark_failed(by_key["image:douyin"], "image_failed")
    blocked = blocked_keys(steps)
    assert "video:douyin" in blocked
    assert by_key["copy:douyin"].status == "succeeded"
    assert by_key["copy:xiaohongshu"].status == "succeeded"
    assert "cards:xiaohongshu" not in blocked
    assert campaign_status(steps) == "failed"


def test_missing_waterproof_fact_cannot_be_asserted():
    facts = locked_fact_snapshot({"waterproof": "needs_confirmation", "material": "帆布"})
    later = dict(facts)
    later["waterproof"] = "confirmed"
    assert facts["waterproof"] == "needs_confirmation"
    assert text_asserts_unconfirmed("这只包防水", facts) == "waterproof"
    assert text_asserts_unconfirmed("帆布托特，适合通勤", facts) is None


def test_topics_cite_sources_and_do_not_republish_original():
    topics = propose_topics(
        [
            {"id": 7, "platform": "taobao", "item_kind": "product", "source_rank": 2, "title": "黑托特"},
            {"id": 3, "platform": "fixture", "item_kind": "product", "source_rank": 1, "title": "棕斜挎"},
            {"id": 9, "platform": "fixture", "item_kind": "product", "source_rank": 4, "title": "钱包"},
            {"id": 8, "platform": "fixture", "item_kind": "product", "source_rank": 3, "title": "旅行袋"},
        ]
    )
    assert [item["source_item_id"] for item in topics] == [3, 7, 8]
    assert all(item["publish_body"] is None for item in topics)


def test_plan_locks_fact_version_and_one_source():
    plan = plan_from_snapshot(
        fact_version_id=4,
        items=[
            {"id": 3, "platform": "fixture", "item_kind": "product", "source_rank": 1, "title": "棕斜挎"},
            {"id": 7, "platform": "taobao", "item_kind": "product", "source_rank": 2, "title": "黑托特"},
        ],
    )
    assert plan["fact_version_id"] == 4
    assert plan["selected_source_item_ids"] == [3]
    assert len(plan["brief"]["candidates"]) == 2
    assert [item["platform"] for item in plan["variants"]] == ["douyin", "xiaohongshu"]
    assert len(plan["steps"]) == 6


def test_plan_can_start_from_owned_product_without_source_and_one_platform():
    plan = plan_from_snapshot(
        fact_version_id=4,
        items=[],
        target_platforms=["xiaohongshu"],
        generation_requirements="突出通勤场景，不补写商品参数",
    )
    assert plan["selected_source_item_ids"] == []
    assert plan["brief"]["target_platforms"] == ["xiaohongshu"]
    assert plan["brief"]["generation_requirements"] == "突出通勤场景，不补写商品参数"
    assert [variant["platform"] for variant in plan["variants"]] == ["xiaohongshu"]
    assert [step.step_key for step in plan["steps"]] == ["brief", "copy:xiaohongshu", "cards:xiaohongshu"]
    assert blocked_keys(plan["steps"]) == set()
