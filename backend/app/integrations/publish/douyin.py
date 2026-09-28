"""网站应用抖音代发 HTTP。创建接受不等于公开发布。"""

from __future__ import annotations

import os
import re
import json
import subprocess
import tempfile
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, UnidentifiedImageError

from app.integrations.publish.base import PublishAdapter, PublishContext, PublishOutcome
from app.integrations.publish.credentials import CredentialError, PublishCredential, resolve_credential

UPLOAD_DOC = "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/upload-video"
CREATE_DOC = "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/video-create"
COVER_DOC = "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-image-text/image-upload/"
COVER_URL = "https://open.douyin.com/api/douyin/v1/video/upload_image/"
UPLOAD_URL = "https://open.douyin.com/api/douyin/v1/video/upload_video/"
CREATE_URL = "https://open.douyin.com/api/douyin/v1/video/create_video/"
SCOPE = "video.create.bind"
DIRECT_LIMIT = 300_000_000  # 官方写 300M，采用较保守的十进制字节阈值。
_SAFE_LOG = re.compile(r"[a-zA-Z0-9_.:-]{1,256}\Z")


def live_calls_enabled() -> bool:
    return os.environ.get("PUBLISH_LIVE") == "1"


def _category(code: int) -> str:
    if code in {28001003, 28001008}:
        return "auth_expired"
    if code in {28001012, 28001014, 28001018, 28001019, 28001015, 2190015}:
        return "permission_denied"
    if code in {28003017, 28003018, 2190020, 2114007}:
        return "rate_limit"
    if code in {2100004, 28001005, 28001006}:
        return "provider_busy"
    if code in {2100005, 2190007, 2114006}:
        return "business_rejected"
    return "business_unknown"


def _copy_text(snapshot: dict) -> str | None:
    title, body, tags = snapshot.get("title"), snapshot.get("body"), snapshot.get("hashtags") or []
    if not isinstance(title, str) or not isinstance(body, str) or not isinstance(tags, list):
        return None
    if any(not isinstance(tag, str) or not tag or "\n" in tag for tag in tags):
        return None
    text = "\n".join(part for part in (title, body, " ".join(f"#{tag.lstrip('#')}" for tag in tags)) if part)
    return text if text and len(text) <= 1000 else None


def _safe_external_id(value: object, credential: PublishCredential) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 256 or credential.access_token in value:
        return None
    return value


def _cover_type(payload: bytes) -> tuple[str, str] | None:
    try:
        with Image.open(BytesIO(payload)) as image:
            format_name = image.format
            image.verify()
        return {"JPEG": ("reviewed-cover.jpg", "image/jpeg"), "PNG": ("reviewed-cover.png", "image/png"),
                "WEBP": ("reviewed-cover.webp", "image/webp")}.get(format_name)
    except (OSError, ValueError, UnidentifiedImageError):
        return None


def _video_type(payload: bytes) -> tuple[str, str] | None:
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temporary:
        temporary.write(payload)
        path = Path(temporary.name)
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration,format_name:stream=codec_type", "-of", "json", str(path)],
            capture_output=True, timeout=20, check=False,
        )
        if probe.returncode != 0:
            return None
        metadata = json.loads(probe.stdout)
        format_info = metadata.get("format") or {}
        formats = set(str(format_info.get("format_name") or "").split(","))
        duration = float(format_info.get("duration") or 0)
        if not 0 < duration <= 900 or not any(stream.get("codec_type") == "video" for stream in metadata.get("streams") or []):
            return None
        if "webm" in formats:
            return ("reviewed-video.webm", "video/webm")
        if formats.intersection({"mov", "mp4"}):
            return ("reviewed-video.mp4", "video/mp4")
        return None
    except (OSError, ValueError, subprocess.TimeoutExpired, KeyError, TypeError):
        return None
    finally:
        path.unlink(missing_ok=True)


class DouyinPublishAdapter(PublishAdapter):
    platform = "douyin"

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._isolated = isinstance(transport, httpx.MockTransport)

    def catalog(self) -> dict[str, Any]:
        return {
            "platform": "douyin", "account_type": "网站应用上完成用户授权的抖音账号，不是样例联系人",
            "scope": SCOPE, "sources": [COVER_DOC, UPLOAD_DOC, CREATE_DOC],
            "limits": ["单次直传视频不超过 300MB；超过需分片（未实现）", "文案与话题合计不超过 1000 字", "创建成功只进入审核，不是公开发布"],
            "endpoints": [
                {"name": "上传审核封面", "method": "POST", "url": COVER_URL, "means_published": False, "verified": True},
                {"name": "上传审核视频", "method": "POST", "url": UPLOAD_URL, "means_published": False, "verified": True},
                {"name": "创建视频", "method": "POST", "url": CREATE_URL, "means_published": False, "verified": True},
            ],
            "server_publish": True, "query_reliable": False,
            "query_note": "网站应用没有可核验的公开发布查询；创建结果不明时不会自动重新提交。",
            "live": live_calls_enabled(), "live_enabled": live_calls_enabled(),
            "live_verified": "pending", "implemented": True,
        }

    def check_capability(self, connection) -> list[str]:
        if connection is None or getattr(connection, "purpose", None) != "publish":
            return ["待连接：没有 purpose=publish 的抖音账号", "待验收：没有可用的 access token 引用"]
        missing = []
        if connection.platform != "douyin":
            missing.append("待连接：这条连接不是抖音")
        if connection.status != "connected":
            missing.append(f"待连接：账号状态是 {connection.status}")
        scopes = connection.scope_set
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes) or SCOPE not in scopes:
            missing.append(f"待连接：缺少用户授权 scope {SCOPE}")
        expires = connection.expires_at
        if expires is None or expires.replace(tzinfo=expires.tzinfo or timezone.utc) <= datetime.now(timezone.utc):
            missing.append("待连接：授权已过期，需要重新授权")
        if not (connection.credential_ref or "").strip():
            missing.append("待验收：没有可用的 access token 引用")
        if not live_calls_enabled():
            missing.append("待验收：真实发布开关关闭，不会调用 open.douyin.com")
        elif not missing:
            try:
                resolve_credential(connection, owner_id=connection.owner_id,
                                   target_open_id=connection.external_account_id)
            except CredentialError:
                missing.append("待连接：部署凭证缺失、过期或账号绑定不符")
        return missing

    def _media(self, ctx: PublishContext, role: str) -> bytes | None:
        if len(ctx.asset_order) != len(ctx.asset_bytes):
            return None
        matches = [payload for entry, payload in zip(ctx.asset_order, ctx.asset_bytes) if entry.get("role") == role]
        return matches[0] if len(matches) == 1 and matches[0] else None

    def execute_stage(self, stage: str, ctx: PublishContext, credential: PublishCredential) -> PublishOutcome:
        """一次只执行一个节点；只有注入 MockTransport 可以在 live off 下隔离测试。"""
        if not live_calls_enabled() and not self._isolated:
            return PublishOutcome(kind="not_ready", error_code="pending_connection", error_message="真实发布开关关闭，没有向平台发送")
        if ctx.ensure_claim is not None and not ctx.ensure_claim():
            return PublishOutcome(kind="unknown", error_code="lease_lost", error_message="领取权失效，停止发送")
        if ctx.ensure_authorization is not None and not ctx.ensure_authorization():
            return PublishOutcome(kind="not_ready", error_code="account_disconnected", error_message="发布账号已断开或重新授权，未向平台发送")
        if not ctx.target_open_id or credential.open_id != ctx.target_open_id:
            return PublishOutcome(kind="failed", error_code="wrong_account", error_message="目标账号与凭证不一致")
        if stage == "cover":
            payload = self._media(ctx, "cover")
            if payload is None:
                return PublishOutcome(kind="failed", error_code="cover_missing", error_message="审核封面不可用")
            if len(payload) > DIRECT_LIMIT:
                return PublishOutcome(kind="failed", error_code="cover_too_large", error_message="封面超过直传上限")
            media_type = _cover_type(payload)
            if media_type is None:
                return PublishOutcome(kind="failed", error_code="cover_invalid", error_message="审核封面不可解码")
            url, content = COVER_URL, {"image": (media_type[0], payload, media_type[1])}
        elif stage == "video":
            payload = self._media(ctx, "final_video")
            if payload is None:
                return PublishOutcome(kind="failed", error_code="video_missing", error_message="审核成片不可用")
            if len(payload) > DIRECT_LIMIT:
                return PublishOutcome(kind="failed", error_code="video_too_large", error_message="视频超过单次直传上限，需要分片")
            media_type = _video_type(payload)
            if media_type is None:
                return PublishOutcome(kind="failed", error_code="video_invalid", error_message="审核视频格式或时长不符合要求")
            url, content = UPLOAD_URL, {"video": (media_type[0], payload, media_type[1])}
        elif stage == "create":
            if not ctx.cover_image_id:
                return PublishOutcome(kind="failed", error_code="cover_missing", error_message="没有已确认的审核封面产物")
            if not ctx.video_upload_id:
                return PublishOutcome(kind="failed", error_code="video_missing", error_message="没有已确认的视频上传产物")
            text = _copy_text(ctx.copy_snapshot)
            if text is None:
                return PublishOutcome(kind="failed", error_code="copy_too_long", error_message="审核文案无效或超过 1000 字")
            url, content = CREATE_URL, {"video_id": ctx.video_upload_id, "custom_cover_image_url": ctx.cover_image_id, "text": text}
        else:
            return PublishOutcome(kind="failed", error_code="stage_invalid", error_message="未知发布阶段")
        if not live_calls_enabled() and not self._isolated:
            return PublishOutcome(kind="not_ready", error_code="pending_connection", error_message="真实发布开关关闭，没有向平台发送")
        if ctx.ensure_claim is not None and not ctx.ensure_claim():
            return PublishOutcome(kind="unknown", error_code="lease_lost", error_message="领取权已失效，停止发送")
        if ctx.ensure_authorization is not None and not ctx.ensure_authorization():
            return PublishOutcome(kind="not_ready", error_code="account_disconnected", error_message="发布账号已断开或重新授权，未向平台发送")
        try:
            with httpx.Client(transport=self._transport, timeout=httpx.Timeout(20.0, read=180.0), follow_redirects=False) as client:
                options = {"params": {"open_id": credential.open_id}, "headers": {"access-token": credential.access_token}}
                response = client.post(url, json=content, **options) if stage == "create" else client.post(url, files=content, **options)
        except httpx.TimeoutException:
            return PublishOutcome(kind="unknown", error_code=f"{stage}_timeout", error_message="平台响应超时，结果不明；停止自动重试")
        except httpx.HTTPError:
            return PublishOutcome(kind="unknown", error_code=f"{stage}_network_unknown", error_message="网络请求结果不明；停止自动重试")
        if response.status_code >= 500:
            return PublishOutcome(kind="unknown", error_code=f"{stage}_server_unknown", error_message="平台服务端错误，结果不明；停止自动重试")
        if response.status_code == 429:
            return PublishOutcome(kind="unknown", error_code=f"{stage}_rate_unknown", error_message="平台限流，结果不明；停止自动重试")
        if 300 <= response.status_code < 400:
            return PublishOutcome(kind="unknown", error_code=f"{stage}_http_unknown", error_message="平台返回重定向，结果不明；停止自动重试")
        if response.status_code == 401:
            return PublishOutcome(kind="failed", error_code="auth_expired", error_message="授权已过期，需要重新授权")
        if response.status_code == 403:
            return PublishOutcome(kind="failed", error_code="permission_denied", error_message="账号缺少发布权限")
        if response.status_code >= 400:
            return PublishOutcome(kind="failed", error_code="business_rejected", error_message="平台拒绝请求，请核对审核内容")
        try:
            decoded = response.json()
        except (ValueError, TypeError):
            decoded = None
        if not isinstance(decoded, dict) or not isinstance(decoded.get("data"), dict) or not isinstance(decoded.get("extra"), dict):
            return PublishOutcome(kind="unknown", error_code=f"{stage}_unknown", error_message="平台返回无法确认，停止自动重试")
        data, extra = decoded["data"], decoded["extra"]
        try:
            code, extra_code = int(data.get("error_code")), int(extra.get("error_code"))
        except (TypeError, ValueError):
            return PublishOutcome(kind="unknown", error_code=f"{stage}_unknown", error_message="平台返回无法确认，停止自动重试")
        raw_log = extra.get("logid")
        log_id = raw_log if isinstance(raw_log, str) and _SAFE_LOG.fullmatch(raw_log) and credential.access_token not in raw_log else None
        if code != 0 or extra_code != 0:
            category = _category(code or extra_code)
            if category in {"provider_busy", "business_unknown"}:
                return PublishOutcome(kind="unknown", error_code=f"{stage}_server_unknown", error_message="平台业务错误，结果不明；停止自动重试", log_id=log_id)
            return PublishOutcome(kind="failed", error_code=category, error_message=f"平台业务拒绝（{category}），请检查账号或内容", log_id=log_id, result={"provider_code": code or extra_code})
        if stage == "cover":
            image = data.get("image")
            artifact = _safe_external_id(image.get("image_id"), credential) if isinstance(image, dict) else None
            return PublishOutcome(kind="cover_uploaded", cover_image_id=artifact, log_id=log_id) if artifact else PublishOutcome(kind="unknown", error_code="cover_unknown", log_id=log_id)
        if stage == "video":
            video = data.get("video")
            artifact = _safe_external_id(video.get("video_id"), credential) if isinstance(video, dict) else None
            return PublishOutcome(kind="video_uploaded", video_upload_id=artifact, log_id=log_id) if artifact else PublishOutcome(kind="unknown", error_code="video_unknown", log_id=log_id)
        item_id = _safe_external_id(data.get("item_id"), credential)
        content_video_id = _safe_external_id(data.get("video_id"), credential) if data.get("video_id") is not None else None
        return PublishOutcome(kind="create_accepted", content_item_id=item_id, content_video_id=content_video_id, log_id=log_id) if item_id else PublishOutcome(kind="unknown", error_code="create_unknown", log_id=log_id)

    def query_status(self, ctx: PublishContext) -> PublishOutcome:
        del ctx
        return PublishOutcome(kind="unknown", error_code="publish_unknown", error_message="网站应用没有可靠公开发布查询")

    def submit(self, ctx: PublishContext) -> PublishOutcome:
        del ctx
        return PublishOutcome(kind="not_ready", error_code="stage_required", error_message="发布需要逐节点持久记录意图")
