"""活动步骤依赖、领取和崩溃恢复。不在请求线程里跑整套生成。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

STEP_DEPS: dict[str, tuple[str, ...]] = {
    "brief": (),
    "copy:douyin": ("brief",),
    "copy:xiaohongshu": ("brief",),
    "image:douyin": ("copy:douyin",),
    "video:douyin": ("image:douyin",),
    "cards:xiaohongshu": ("copy:xiaohongshu",),
}

STEP_PLATFORM = {
    "brief": "",
    "copy:douyin": "douyin",
    "copy:xiaohongshu": "xiaohongshu",
    "image:douyin": "douyin",
    "video:douyin": "douyin",
    "cards:xiaohongshu": "xiaohongshu",
}
TARGET_PLATFORMS = ("douyin", "xiaohongshu")


def selected_platforms(target_platforms: list[str] | tuple[str, ...] | None) -> list[str]:
    platforms = list(TARGET_PLATFORMS if target_platforms is None else target_platforms)
    if not platforms or len(platforms) != len(set(platforms)) or any(
        platform not in TARGET_PLATFORMS for platform in platforms
    ):
        raise ValueError("目标平台必须选择抖音或小红书，且不能重复")
    return platforms


@dataclass
class StepState:
    step_key: str
    variant_platform: str
    version: int = 1
    status: str = "pending"
    local_request_id: str | None = None
    provider_request_id: str | None = None
    attempt: int = 0
    lease_until: datetime | None = None
    error_code: str | None = None
    output: dict = field(default_factory=dict)
    needs_reconcile: bool = False

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self.step_key, self.variant_platform, self.version)


def new_recipe(target_platforms: list[str] | tuple[str, ...] | None = None) -> list[StepState]:
    chosen = set(selected_platforms(target_platforms))
    steps = []
    for key, platform in STEP_PLATFORM.items():
        if not platform or platform in chosen:
            steps.append(StepState(step_key=key, variant_platform=platform))
    return steps


def _by_key(steps: list[StepState]) -> dict[str, StepState]:
    return {step.step_key: step for step in steps}


def deps_succeeded(step: StepState, steps: list[StepState]) -> bool:
    index = _by_key(steps)
    return all(index[name].status == "succeeded" for name in STEP_DEPS[step.step_key])


def claim_one(steps: list[StepState], *, now: datetime, held: set[tuple[str, str, int]]) -> StepState | None:
    """同一时刻只有一个领取者能拿走某一步。依赖未成功的步骤不会被领。"""
    for step in steps:
        if step.status != "pending" or step.identity in held:
            continue
        if not deps_succeeded(step, steps):
            continue
        step.status = "running"
        step.attempt += 1
        step.lease_until = now + timedelta(minutes=2)
        held.add(step.identity)
        return step
    return None


def begin_submit(step: StepState) -> str:
    """调用外部模型之前先落下本地请求号。"""
    if step.local_request_id is None:
        step.local_request_id = uuid.uuid4().hex
    return step.local_request_id


def note_provider_request(step: StepState, provider_request_id: str) -> None:
    step.provider_request_id = provider_request_id


def crash_before_ack(step: StepState) -> str:
    """进程死在『可能已经提交、但任务号还没落库』时，标 unknown，禁止自动再交。"""
    if step.status != "running":
        return step.status
    if step.provider_request_id:
        step.needs_reconcile = True
        return "reconcile"
    step.status = "unknown"
    step.error_code = "submit_unacked"
    return "unknown"


def recoverable(steps: list[StepState]) -> list[StepState]:
    return [step for step in steps if step.needs_reconcile and step.provider_request_id]


def mark_succeeded(step: StepState, output: dict) -> None:
    step.status = "succeeded"
    step.output = output
    step.needs_reconcile = False
    step.lease_until = None


def mark_failed(step: StepState, code: str) -> None:
    step.status = "failed"
    step.error_code = code
    step.lease_until = None


def blocked_keys(steps: list[StepState]) -> set[str]:
    """失败步骤会挡住依赖它的后续步骤，已经成功的步骤保持成功。"""
    index = _by_key(steps)
    blocked: set[str] = set()
    changed = True
    while changed:
        changed = False
        for key, deps in STEP_DEPS.items():
            if key not in index or key in blocked:
                continue
            if any(name in blocked or index[name].status in {"failed", "unknown"} for name in deps):
                blocked.add(key)
                changed = True
    return blocked


def text_asserts_unconfirmed(text: str, facts: dict) -> str | None:
    """缺防水事实时，文案不能写成已经防水。有依据的「不防水」要放过。"""
    from app.services.consumer_qc import _waterproof_issue

    issue = _waterproof_issue(text, facts)
    if issue is None:
        return None
    return "waterproof"


def locked_fact_snapshot(facts: dict) -> dict:
    return dict(facts)


def input_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def propose_topics(items: list[dict], *, limit: int = 3) -> list[dict]:
    """只从已采集样本里选题，引用 source id，不把原文当成待发布正文。"""
    ranked = sorted(items, key=lambda item: int(item.get("source_rank") or 10**6))
    topics = []
    for item in ranked[:limit]:
        topics.append(
            {
                "source_item_id": item["id"],
                "platform": item["platform"],
                "item_kind": item["item_kind"],
                "angle": "场景和卖点表达",
                "cite": item.get("title") or "",
                "publish_body": None,
            }
        )
    return topics


def plan_from_snapshot(
    *, fact_version_id: int, items: list[dict],
    target_platforms: list[str] | None = None,
    generation_requirements: str = "",
) -> dict:
    """锁定事实版本和来源引用。按来源顺序执行首条，保留最多 3 个候选。"""
    topics = propose_topics(items, limit=3)
    chosen = topics[:1]
    platforms = selected_platforms(target_platforms)
    return {
        "fact_version_id": fact_version_id,
        "brief": {
            "candidates": topics,
            "execute": chosen,
            "target_platforms": platforms,
            "generation_requirements": generation_requirements,
        },
        "selected_source_item_ids": [item["source_item_id"] for item in chosen],
        "steps": new_recipe(platforms),
        "variants": [
            {"platform": platform, "content_type": "short_video" if platform == "douyin" else "note", "version": 1}
            for platform in platforms
        ],
    }


def campaign_status(steps: list[StepState]) -> str:
    if any(step.status == "unknown" for step in steps):
        return "blocked"
    if any(step.status == "failed" for step in steps):
        return "failed"
    if all(step.status == "succeeded" for step in steps):
        return "needs_review"
    if any(step.status == "running" for step in steps):
        return "generating"
    return "planning"
