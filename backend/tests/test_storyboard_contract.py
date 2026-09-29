"""不依赖数据库的分镜完整性与导出命名契约。"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.services.delivery import DeliveryBlocked, _filename
from app.services.publish_jobs import _storyboard_publish_blocker
from app.services.storyboard_review import _assert_replaceable_tasks, approved_assets
from app.services.variant_review import ReviewBlocked


def _storyboard():
    return {
        "mode": "reviewed_shots_v1",
        "shots": [
            {
                "locked": True,
                "first_frame_review": "approved",
                "video_review": "approved",
                "first_frame_asset_id": index * 2 + 1,
                "video_asset_id": index * 2 + 2,
            }
            for index in range(3)
        ],
    }


def test_three_locked_reviewed_shots_have_six_ordered_assets():
    assert approved_assets(_storyboard()) == [
        ("shot_first_frame", 0, 1), ("shot_video", 0, 2),
        ("shot_first_frame", 1, 3), ("shot_video", 1, 4),
        ("shot_first_frame", 2, 5), ("shot_video", 2, 6),
    ]


@pytest.mark.parametrize("mutation", [
    lambda board: board["shots"].pop(),
    lambda board: board["shots"][1].update(locked=False),
    lambda board: board["shots"][1].update(video_review=None),
    lambda board: board["shots"][1].update(first_frame_asset_id=None),
])
def test_missing_or_unreviewed_shot_cannot_be_approved(mutation):
    board = _storyboard()
    mutation(board)
    assert approved_assets(board) is None


def test_export_names_are_individual_shots_not_a_final_film():
    assert _filename(0, "shot_first_frame", "image/png") == "shots/01-first-frame.png"
    assert _filename(1, "shot_video", "video/mp4") == "shots/01-video.mp4"
    assert _filename(4, "shot_first_frame", "image/jpeg") == "shots/03-first-frame.jpg"
    assert _filename(5, "shot_video", "video/mp4") == "shots/03-video.mp4"
    with pytest.raises(DeliveryBlocked):
        _filename(5, "shot_video", "image/png")
    with pytest.raises(DeliveryBlocked):
        _filename(4, "shot_first_frame", "video/mp4")



def test_three_shot_export_does_not_claim_publishable_film():
    class Variant:
        storyboard = _storyboard()

    assert _storyboard_publish_blocker(Variant())["code"] == "storyboard_not_composited"
    Variant.storyboard = {"mode": "legacy"}
    assert _storyboard_publish_blocker(Variant()) is None


@pytest.mark.parametrize("status", ["queued", "running", "unknown"])
def test_redo_cannot_orphan_chargeable_first_frame_or_video_task(status):
    class Session:
        def get(self, _model, task_id):
            return SimpleNamespace(status=status if task_id == 2 else "succeeded")

    shot = {"first_frame_task_id": 1, "video_task_id": 2}
    with pytest.raises(ReviewBlocked) as error:
        _assert_replaceable_tasks(Session(), shot, first_frame=True)
    assert error.value.code == "result_unknown"
    with pytest.raises(ReviewBlocked):
        _assert_replaceable_tasks(Session(), shot, first_frame=False)


def test_redo_video_keeps_completed_frame_and_rejects_missing_task_record():
    class Session:
        def get(self, _model, task_id):
            return SimpleNamespace(status="succeeded") if task_id == 1 else None

    shot = {"first_frame_task_id": 1, "video_task_id": 2}
    with pytest.raises(ReviewBlocked):
        _assert_replaceable_tasks(Session(), shot, first_frame=False)
    _assert_replaceable_tasks(Session(), {"first_frame_task_id": 1, "video_task_id": None}, first_frame=True)
