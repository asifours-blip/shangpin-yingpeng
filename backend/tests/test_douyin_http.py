"""抖音 HTTP 契约走真实 httpx 组装和响应解析，网络只进 MockTransport。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.integrations.publish.base import PublishContext
from app.integrations.publish.credentials import PublishCredential
from app.integrations.publish.douyin import DouyinPublishAdapter


VIDEO = (Path(__file__).parent / "fixtures" / "publish-one-second.mp4").read_bytes()
COVER = (Path(__file__).parent / "fixtures" / "canvas-tote.jpg").read_bytes()
CREDENTIAL = PublishCredential("synthetic-token-never-log", "bound-open-id", "isolated-app")


def _context(**changes):
    values = dict(
        platform="douyin", copy_snapshot={"title": "帆布托特", "body": "材质是帆布。", "hashtags": ["箱包"]},
        asset_order=[{"asset_id": 1, "role": "cover", "position": 0}, {"asset_id": 2, "role": "final_video", "position": 1}],
        asset_bytes=(COVER, VIDEO), local_request_id="local-only", connection_id=19,
        target_open_id="bound-open-id", cover_image_id=None, video_upload_id=None,
    )
    values.update(changes)
    return PublishContext(**values)


def _success(key, value):
    return httpx.Response(200, json={"data": {"error_code": 0, key: value}, "extra": {"error_code": 0, "logid": "safe-log-1"}})


def test_cover_video_create_send_reviewed_bytes_text_and_distinct_ids(monkeypatch):
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.params["open_id"] == "bound-open-id"
        assert request.headers["access-token"] == CREDENTIAL.access_token
        if request.url.path.endswith("upload_image/"):
            assert b'name="image"' in request.content
            assert COVER in request.content
            assert b'filename="reviewed-cover.jpg"' in request.content
            assert b'Content-Type: image/jpeg' in request.content
            return _success("image", {"image_id": "cover-id"})
        if request.url.path.endswith("upload_video/"):
            assert b'name="video"' in request.content
            assert VIDEO in request.content
            assert b'filename="reviewed-video.mp4"' in request.content
            assert b'Content-Type: video/mp4' in request.content
            return _success("video", {"video_id": "upload-video-id"})
        assert request.url.path.endswith("create_video/")
        payload = json.loads(request.content)
        assert payload == {
            "video_id": "upload-video-id", "custom_cover_image_url": "cover-id",
            "text": "帆布托特\n材质是帆布。\n#箱包",
        }
        return _success("item_id", "content-item-id")

    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(handle))
    cover = adapter.execute_stage("cover", _context(), CREDENTIAL)
    assert cover.kind == "cover_uploaded" and cover.cover_image_id == "cover-id"
    assert cover.log_id == "safe-log-1"
    video = adapter.execute_stage("video", _context(cover_image_id=cover.cover_image_id), CREDENTIAL)
    assert video.kind == "video_uploaded" and video.video_upload_id == "upload-video-id"
    created = adapter.execute_stage(
        "create", _context(cover_image_id=cover.cover_image_id, video_upload_id=video.video_upload_id), CREDENTIAL,
    )
    assert created.kind == "create_accepted" and created.content_item_id == "content-item-id"
    assert len(requests) == 3
    assert "synthetic-token-never-log" not in repr(created)


def test_http_200_business_error_is_not_a_success_and_sensitive_body_is_discarded(monkeypatch):
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)
    response = httpx.Response(200, json={
        "data": {"error_code": 28001003, "description": "synthetic-token-never-log"},
        "extra": {"error_code": 28001003, "logid": "safe-log-2"},
    })
    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(lambda _request: response))
    outcome = adapter.execute_stage("cover", _context(), CREDENTIAL)
    assert outcome.kind == "failed" and outcome.error_code == "auth_expired"
    assert outcome.log_id == "safe-log-2"
    assert "synthetic-token-never-log" not in repr(outcome)


def test_create_timeout_is_unknown_and_does_not_expose_credentials(monkeypatch):
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)

    def timeout(request):
        raise httpx.ReadTimeout("synthetic-token-never-log", request=request)

    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(timeout))
    outcome = adapter.execute_stage(
        "create", _context(cover_image_id="cover-id", video_upload_id="upload-video-id"), CREDENTIAL,
    )
    assert outcome.kind == "unknown" and outcome.error_code == "create_timeout"
    assert "synthetic-token-never-log" not in repr(outcome)


def test_missing_cover_and_oversize_copy_block_before_any_http(monkeypatch):
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)
    requests = []
    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(lambda request: requests.append(request)))
    missing = adapter.execute_stage("create", _context(video_upload_id="upload-video-id"), CREDENTIAL)
    assert missing.kind == "failed" and missing.error_code == "cover_missing"
    long_copy = adapter.execute_stage("create", _context(
        cover_image_id="cover-id", video_upload_id="upload-video-id",
        copy_snapshot={"title": "x" * 1001, "body": "", "hashtags": []},
    ), CREDENTIAL)
    assert long_copy.kind == "failed" and long_copy.error_code == "copy_too_long"
    assert requests == []


def test_200_provider_busy_and_redirect_cannot_be_mistaken_for_success(monkeypatch):
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)
    busy = httpx.Response(200, json={"data": {"error_code": 2100004}, "extra": {"error_code": 0}})
    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(lambda _request: busy))
    outcome = adapter.execute_stage("create", _context(cover_image_id="cover-id", video_upload_id="video-id"), CREDENTIAL)
    assert outcome.kind == "unknown" and outcome.error_code == "create_server_unknown"
    redirect = DouyinPublishAdapter(transport=httpx.MockTransport(lambda _request: httpx.Response(302, json={
        "data": {"error_code": 0, "image": {"image_id": "wrong-success"}}, "extra": {"error_code": 0},
    })))
    outcome = redirect.execute_stage("cover", _context(), CREDENTIAL)
    assert outcome.kind == "unknown" and outcome.cover_image_id is None


def test_response_ids_and_log_id_cannot_reflect_access_token(monkeypatch):
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)
    reflected = httpx.Response(200, json={"data": {"error_code": 0, "image": {"image_id": CREDENTIAL.access_token}},
                                          "extra": {"error_code": 0, "logid": CREDENTIAL.access_token}})
    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(lambda _request: reflected))
    outcome = adapter.execute_stage("cover", _context(), CREDENTIAL)
    assert outcome.kind == "unknown" and outcome.log_id is None
    assert CREDENTIAL.access_token not in repr(outcome)


@pytest.mark.parametrize(
    ("response", "expected_kind", "expected_code"), [
        (httpx.Response(401, text="synthetic-token-never-log"), "failed", "auth_expired"),
        (httpx.Response(403, text="synthetic-token-never-log"), "failed", "permission_denied"),
        (httpx.Response(429), "unknown", "create_rate_unknown"),
        (httpx.Response(503), "unknown", "create_server_unknown"),
        (httpx.Response(200, json={"data": {"error_code": 28003018}, "extra": {"error_code": 0}}), "failed", "rate_limit"),
        (httpx.Response(200, json={"data": {"error_code": 2190007}, "extra": {"error_code": 0}}), "failed", "business_rejected"),
        (httpx.Response(200, json={"data": {"error_code": 79999999}, "extra": {"error_code": 0}}), "unknown", "create_server_unknown"),
    ],
)
def test_create_errors_are_classified_without_raw_body(monkeypatch, response, expected_kind, expected_code):
    monkeypatch.delenv("PUBLISH_LIVE", raising=False)
    adapter = DouyinPublishAdapter(transport=httpx.MockTransport(lambda _request: response))
    outcome = adapter.execute_stage("create", _context(cover_image_id="cover-id", video_upload_id="video-id"), CREDENTIAL)
    assert (outcome.kind, outcome.error_code) == (expected_kind, expected_code)
    assert "synthetic-token-never-log" not in repr(outcome)
