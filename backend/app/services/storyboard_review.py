"""三镜头审核：外部任务只写 GenerationTask，活动版本只由用户操作推进。"""

from __future__ import annotations

from copy import deepcopy
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import GenerationTask, GenerationTaskAsset, ImageAsset, User
from app.models.campaign import ContentVariant, PipelineStep, VariantAsset
from app.models.product import OwnedProduct
from app.services.freeze_ops import lock_user_for_update
from app.services.publish_jobs import invalidate_unsubmitted
from app.services.variant_review import (
    ReviewBlocked,
    _commit_locked,
    _fact,
    _fork_variant,
    _latest_platform_steps,
    _lock_campaign,
    _locked_current,
    _run,
)

MODE = "reviewed_shots_v1"
ANGLES = ("正面主视觉", "材质细节", "侧面展示")
ASSET_ROLES = (("shot_first_frame", "first_frame_asset_id"), ("shot_video", "video_asset_id"))


def _shot(storyboard: dict, shot_index: int) -> dict:
    shots = storyboard.get("shots")
    if not isinstance(shots, list) or len(shots) != 3 or shot_index not in (0, 1, 2):
        raise ReviewBlocked("shot_missing", "只能选择三个镜头中的一个")
    return shots[shot_index]


def approved_assets(storyboard: dict) -> list[tuple[str, int, int]] | None:
    shots = storyboard.get("shots")
    if not isinstance(shots, list) or len(shots) != 3:
        return None
    assets: list[tuple[str, int, int]] = []
    for position, shot in enumerate(shots):
        if not isinstance(shot, dict) or not shot.get("locked"):
            return None
        if shot.get("first_frame_review") != "approved" or shot.get("video_review") != "approved":
            return None
        for role, field in ASSET_ROLES:
            asset_id = shot.get(field)
            if not isinstance(asset_id, int) or asset_id <= 0:
                return None
            assets.append((role, position, asset_id))
    return assets


def _output_asset(db: Session, task_id: int, owner_id: int, mode: str, input_asset_id: int) -> ImageAsset:
    task = db.get(GenerationTask, task_id)
    if task is None or task.user_id != owner_id or task.mode != mode or task.status != "succeeded":
        raise ReviewBlocked("task_incomplete", "绑定的生成任务未成功")
    links = list(db.scalars(select(GenerationTaskAsset).where(GenerationTaskAsset.task_id == task_id)).all())
    if len([item for item in links if item.role == "product" and item.asset_id == input_asset_id]) != 1:
        raise ReviewBlocked("task_input_changed", "生成任务输入与锁定素材不一致")
    outputs = [item for item in links if item.role == "output"]
    if len(outputs) != 1:
        raise ReviewBlocked("task_output_missing", "生成任务缺少唯一输出")
    asset = db.get(ImageAsset, outputs[0].asset_id)
    expected = "image/" if mode == "i2i" else "video/mp4"
    if asset is None or asset.owner_id != owner_id or asset.bucket != settings.MINIO_BUCKET or not asset.mime.startswith(expected):
        raise ReviewBlocked("task_output_invalid", "生成输出不属于当前用户或媒体类型不符")
    return asset


def _assert_replaceable_tasks(db: Session, shot: dict, *, first_frame: bool) -> None:
    fields = ("first_frame_task_id", "video_task_id") if first_frame else ("video_task_id",)
    for field in fields:
        task_id = shot.get(field)
        if task_id is None:
            continue
        task = db.get(GenerationTask, task_id)
        if task is None or task.status in {"queued", "running", "unknown"}:
            raise ReviewBlocked("result_unknown", "相关生成仍排队、运行或结果未知，先核对上游，不得重复计费提交")


def _copy_assets(db: Session, variant: ContentVariant) -> None:
    for position, shot in enumerate(variant.storyboard["shots"]):
        for role, field in ASSET_ROLES:
            asset_id = shot.get(field)
            if asset_id is not None and shot.get("first_frame_review") == "approved" and (
                role == "shot_first_frame" or shot.get("video_review") == "approved"
            ):
                db.add(VariantAsset(variant_id=variant.id, asset_id=asset_id, role=role, position=position))


def _fork(db: Session, campaign, current: ContentVariant, storyboard: dict) -> ContentVariant:
    invalidate_unsubmitted(db, current.id)
    created = _fork_variant(
        db, current, title=None, body=None, hashtags=None,
        fact_version_id=current.fact_version_id, clear_copy=False,
    )
    created.storyboard = storyboard
    created.qc_result = {"storyboard": deepcopy(storyboard)}
    created.status = "needs_review"
    _copy_assets(db, created)
    campaign.status = "needs_review"
    _commit_locked(db)
    db.refresh(created)
    return created


def _locked(db: Session, user: User, campaign_id: int, expected_version: int):
    campaign = _lock_campaign(db, user, campaign_id)
    if campaign.owner_id != user.id:
        raise ReviewBlocked("owner_required", "仅活动所属账号可操作分镜")
    current = _locked_current(db, campaign_id, "douyin", expected_version)
    return campaign, current


def start_storyboard(db: Session, user: User, campaign_id: int, expected_version: int) -> ContentVariant:
    campaign, current = _locked(db, user, campaign_id, expected_version)
    if current.storyboard and current.storyboard.get("mode") == MODE:
        raise ReviewBlocked("storyboard_exists", "这一版已经进入三镜头审核")
    run = _run(db, campaign.id)
    steps = _latest_platform_steps(db, run.id, "douyin")
    brief = db.scalar(select(PipelineStep).where(
        PipelineStep.run_id == run.id, PipelineStep.step_key == "brief",
    ).order_by(PipelineStep.version.desc()))
    if brief is None or brief.status != "succeeded" or {step.step_key for step in steps} != {"copy:douyin", "image:douyin", "video:douyin"} or any(step.status != "succeeded" for step in steps):
        raise ReviewBlocked("pipeline_incomplete", "先完成现有活动生成，避免两条媒体流水线同时写入")
    if run.generation_budget - run.budget_reserved < 6:
        raise ReviewBlocked("storyboard_budget", "三镜头首帧与视频需预留 6 次生成预算，请新建预算足够的活动")
    product = db.get(OwnedProduct, campaign.product_id)
    if product is None or product.primary_asset_id is None:
        raise ReviewBlocked("product_image_missing", "商品原图缺失")
    fact = _fact(db, current, campaign)
    storyboard = {
        "mode": MODE,
        "product_asset_id": product.primary_asset_id,
        "fact_version_id": fact.id,
        "shots": [
            {
                "prompt": f"{product.name}，{angle}，保持商品外观与原图一致，不添加未经确认的文字或功能宣称",
                "first_frame_task_id": None,
                "first_frame_asset_id": None,
                "first_frame_review": None,
                "video_task_id": None,
                "video_asset_id": None,
                "video_review": None,
                "locked": False,
            }
            for angle in ANGLES
        ],
    }
    return _fork(db, campaign, current, storyboard)


def _queue_task(db: Session, owner_id: int, mode: str, prompt: str, input_asset_id: int) -> GenerationTask:
    if not settings.ARK_API_KEY or not (settings.ARK_IMAGE_ENDPOINT if mode == "i2i" else settings.ARK_VIDEO_ENDPOINT):
        raise ReviewBlocked("provider_unavailable", "相应的方舟模型未配置，不能提交真实生成")
    owner = lock_user_for_update(db, owner_id)
    if not owner.is_active or owner.is_frozen:
        raise ReviewBlocked("owner_unavailable", "活动所属账号不可提交生成任务")
    asset = db.get(ImageAsset, input_asset_id)
    if asset is None or asset.owner_id != owner_id or asset.bucket != settings.MINIO_BUCKET or not asset.mime.startswith("image/"):
        raise ReviewBlocked("input_asset_invalid", "参考图不存在、不属于当前用户或不是图片")
    if not 1 <= len(prompt.strip()) <= 4000:
        raise ReviewBlocked("prompt_invalid", "镜头描述长度须为 1 至 4000 字")
    params = (
        {"size": "2048x2048", "watermark": False, "output_format": "png", "response_format": "url", "model": "ARK_IMAGE_ENDPOINT"}
        if mode == "i2i"
        else {"duration": 5, "resolution": "480p", "ratio": "16:9", "watermark": False, "model": "ARK_VIDEO_ENDPOINT"}
    )
    task = GenerationTask(user_id=owner_id, mode=mode, prompt=prompt, params=params, status="queued", review_status="unreviewed")
    db.add(task)
    db.flush()
    db.add(GenerationTaskAsset(task_id=task.id, asset_id=input_asset_id, role="product", position=0))
    return task


def change_shot(
    db: Session, user: User, campaign_id: int, expected_version: int, shot_index: int,
    action: str, *, prompt: str | None = None, accepted: bool | None = None,
    stage: str | None = None,
) -> ContentVariant:
    campaign, current = _locked(db, user, campaign_id, expected_version)
    if not current.storyboard or current.storyboard.get("mode") != MODE:
        raise ReviewBlocked("storyboard_missing", "请先开启三镜头审核")
    storyboard = deepcopy(current.storyboard)
    shot = _shot(storyboard, shot_index)
    if action == "unlock":
        if not shot["locked"]:
            raise ReviewBlocked("shot_not_locked", "镜头尚未锁定")
        shot["locked"] = False
    elif action == "lock":
        if shot["locked"] or shot.get("first_frame_review") != "approved" or shot.get("video_review") != "approved":
            raise ReviewBlocked("shot_incomplete", "首帧与视频均审核通过后才能锁定")
        shot["locked"] = True
    else:
        if shot["locked"]:
            raise ReviewBlocked("shot_locked", "镜头已锁定，请显式解锁后重做")
        if action == "redo":
            if stage not in {"first_frame", "video"}:
                raise ReviewBlocked("stage_invalid", "重做阶段只能是首帧或视频")
            _assert_replaceable_tasks(db, shot, first_frame=stage == "first_frame")
            if stage == "first_frame":
                if prompt is not None:
                    cleaned = prompt.strip()
                    if not 1 <= len(cleaned) <= 4000:
                        raise ReviewBlocked("prompt_invalid", "镜头描述长度须为 1 至 4000 字")
                    shot["prompt"] = cleaned
                shot.update(first_frame_task_id=None, first_frame_asset_id=None, first_frame_review=None)
            shot.update(video_task_id=None, video_asset_id=None, video_review=None)
        elif action == "submit_frame":
            if shot.get("first_frame_task_id") is not None:
                raise ReviewBlocked("redo_required", "已有首帧任务；要重做请先明确选择本镜头")
            run = _run(db, campaign.id)
            if run.budget_reserved >= run.generation_budget:
                raise ReviewBlocked("budget_exhausted", "活动生成预算已用尽")
            task = _queue_task(db, campaign.owner_id, "i2i", shot["prompt"], storyboard["product_asset_id"])
            run.budget_reserved += 1
            shot["first_frame_task_id"] = task.id
        elif action == "review_frame":
            if shot.get("first_frame_review") is not None:
                raise ReviewBlocked("redo_required", "这一版首帧已审核，要改决定请明确重做")
            task_id = shot.get("first_frame_task_id")
            if task_id is None or accepted is None:
                raise ReviewBlocked("review_missing", "没有可审核的首帧任务")
            asset = _output_asset(db, task_id, campaign.owner_id, "i2i", storyboard["product_asset_id"])
            _assert_replaceable_tasks(db, shot, first_frame=False)
            shot["first_frame_review"] = "approved" if accepted else "rejected"
            shot["first_frame_asset_id"] = asset.id if accepted else None
            shot.update(video_task_id=None, video_asset_id=None, video_review=None)
        elif action == "submit_video":
            if shot.get("first_frame_review") != "approved" or shot.get("first_frame_asset_id") is None:
                raise ReviewBlocked("frame_unreviewed", "先审核通过本镜头首帧")
            if shot.get("video_task_id") is not None:
                raise ReviewBlocked("redo_required", "已有视频任务；要重做请先明确选择本镜头")
            run = _run(db, campaign.id)
            if run.budget_reserved >= run.generation_budget:
                raise ReviewBlocked("budget_exhausted", "活动生成预算已用尽")
            task = _queue_task(db, campaign.owner_id, "i2v", shot["prompt"], shot["first_frame_asset_id"])
            run.budget_reserved += 1
            shot["video_task_id"] = task.id
        elif action == "review_video":
            if shot.get("video_review") is not None:
                raise ReviewBlocked("redo_required", "这一版视频已审核，要改决定请明确重做")
            task_id = shot.get("video_task_id")
            if task_id is None or accepted is None or shot.get("first_frame_review") != "approved":
                raise ReviewBlocked("review_missing", "没有可审核的视频任务")
            asset = _output_asset(db, task_id, campaign.owner_id, "i2v", shot["first_frame_asset_id"])
            shot["video_review"] = "approved" if accepted else "rejected"
            shot["video_asset_id"] = asset.id if accepted else None
        else:
            raise ReviewBlocked("action_invalid", "不支持的镜头操作")
    storyboard["shots"][shot_index] = shot
    return _fork(db, campaign, current, storyboard)


def storyboard_tasks(db: Session, storyboard: dict | None, owner_id: int) -> dict[str, dict]:
    if not storyboard or storyboard.get("mode") != MODE:
        return {}
    task_ids = {
        int(task_id)
        for shot in storyboard.get("shots", [])
        for task_id in (shot.get("first_frame_task_id"), shot.get("video_task_id"))
        if task_id is not None
    }
    tasks = db.scalars(select(GenerationTask).where(GenerationTask.id.in_(task_ids), GenerationTask.user_id == owner_id)).all()
    return {
        str(task.id): {"status": task.status, "error_message": task.error_message}
        for task in tasks
    }
