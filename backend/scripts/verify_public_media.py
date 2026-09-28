"""Offline check for the independently generated public media fixtures.

Run from backend/ in an environment with the backend requirements, ffprobe,
and Pillow installed. No database, object store, or outbound network is used.
"""

import json
import subprocess
import sys
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.integrations.publish.base import PublishContext
from app.integrations.publish.credentials import PublishCredential
from app.integrations.publish.douyin import DouyinPublishAdapter


fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
cover = (fixtures / "canvas-tote.jpg").read_bytes()
video_path = fixtures / "publish-one-second.mp4"
video = video_path.read_bytes()

with Image.open(fixtures / "canvas-tote.jpg") as image:
    image.verify()

probe = json.loads(subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries",
     "format=duration,format_name:stream=codec_type", "-of", "json", str(video_path)],
    capture_output=True, check=True, text=True,
).stdout)
assert any(stream["codec_type"] == "video" for stream in probe["streams"])
assert float(probe["format"]["duration"]) == 1.0

requests = []


def reply(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    if request.url.path.endswith("upload_image/"):
        assert cover in request.content
        key, value = "image", {"image_id": "cover-id"}
    elif request.url.path.endswith("upload_video/"):
        assert video in request.content
        key, value = "video", {"video_id": "video-id"}
    else:
        assert request.url.path.endswith("create_video/")
        assert json.loads(request.content)["text"] == "帆布托特\n材质是帆布。\n#箱包"
        key, value = "item_id", "item-id"
    return httpx.Response(200, json={
        "data": {"error_code": 0, key: value},
        "extra": {"error_code": 0, "logid": "safe-log"},
    })


def context(cover_id=None, video_id=None) -> PublishContext:
    return PublishContext(
        platform="douyin",
        copy_snapshot={"title": "帆布托特", "body": "材质是帆布。", "hashtags": ["箱包"]},
        asset_order=[
            {"asset_id": 1, "role": "cover", "position": 0},
            {"asset_id": 2, "role": "final_video", "position": 1},
        ],
        asset_bytes=(cover, video),
        local_request_id="local-only", connection_id=19,
        target_open_id="bound-open-id", cover_image_id=cover_id,
        video_upload_id=video_id,
    )


credential = PublishCredential("synthetic-token-never-log", "bound-open-id", "isolated-app")
adapter = DouyinPublishAdapter(transport=httpx.MockTransport(reply))
uploaded_cover = adapter.execute_stage("cover", context(), credential)
uploaded_video = adapter.execute_stage("video", context(uploaded_cover.cover_image_id), credential)
created = adapter.execute_stage(
    "create", context(uploaded_cover.cover_image_id, uploaded_video.video_upload_id), credential,
)
assert (uploaded_cover.kind, uploaded_video.kind, created.kind) == (
    "cover_uploaded", "video_uploaded", "create_accepted",
)
assert len(requests) == 3
print("synthetic_jpeg=valid synthetic_mp4=valid ffprobe_duration=1.0 http_mock_stages=3 passed")
