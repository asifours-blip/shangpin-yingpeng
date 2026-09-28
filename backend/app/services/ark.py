"""火山方舟文生图 / 参考图生图 / 图生视频 / Chat。浏览器不直连。TOS URL 必须 GET 下载（HEAD=403）。"""

import base64
import time
from typing import Any, Callable

import httpx

from app.core.config import settings

# 已验证：lite 最低约 2560×1440 总像素；1024x1024 会 400。只开放实测过的档。
ALLOWED_SIZES = {"2048x2048", "2K"}
ARK_TIMEOUT = httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0)
# 反推 / Prompt 优化：写死已开通的 Chat 模型，不用生图接入点，也不再调 evolving。
ARK_CHAT_MODEL = "doubao-seed-2-1-pro-260628"
CHAT_TIMEOUT = httpx.Timeout(connect=30.0, read=90.0, write=30.0, pool=30.0)
VIDEO_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)
VIDEO_POLL_INTERVAL = 5.0
VIDEO_POLL_TIMEOUT = 600.0
ALLOWED_VIDEO_RESOLUTIONS = {"480p", "720p"}
ALLOWED_VIDEO_DURATIONS = {5}
MIN_IMAGE_SIDE = 14

# 文档要求 data:image/<小写格式>;base64, ；jpg 必须写成 jpeg。
_MIME_TO_FMT = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/gif": "gif",
    "image/tiff": "tiff",
    "image/heic": "heic",
    "image/heif": "heif",
}


class ArkError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def bytes_to_data_url(raw: bytes, mime: str) -> str:
    """MinIO 字节 → Ark 可接受的 Base64 data URL。禁止传 localhost MinIO URL。"""
    mime_norm = (mime or "image/png").split(";")[0].strip().lower()
    fmt = _MIME_TO_FMT.get(mime_norm)
    if fmt is None:
        subtype = mime_norm.split("/")[-1] if "/" in mime_norm else "png"
        fmt = "jpeg" if subtype == "jpg" else subtype
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/{fmt};base64,{b64}"


def generate_t2i(prompt: str, size: str) -> dict[str, Any]:
    """调用方舟文生图，返回原始 JSON。失败抛 ArkError。"""
    payload = _base_payload(prompt, size)
    return _post_generations(payload)


def generate_i2i(prompt: str, size: str, images: list[str]) -> dict[str, Any]:
    """参考图生图。images 必须是 data URL。1 张传字符串，2 张传数组（与实测一致）。"""
    if not (1 <= len(images) <= 2):
        raise ArkError("参考图必须是 1 张或 2 张")
    for item in images:
        if not item.startswith("data:image/"):
            raise ArkError("参考图必须是 Base64 data URL，不能传 MinIO / localhost 地址")
    payload = _base_payload(prompt, size)
    payload["image"] = images[0] if len(images) == 1 else images
    return _post_generations(payload)


def chat_text(system: str, user: str, *, max_tokens: int = 1024) -> str:
    """纯文本 Chat。成功只认 HTTP 200 且有文本。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return _post_chat(messages, max_tokens=max_tokens)


def chat_vision(system: str, user: str, data_url: str, *, max_tokens: int = 1024) -> str:
    """视觉 Chat。图必须是 Base64 data URL，禁止传 MinIO / localhost。"""
    if not data_url.startswith("data:image/"):
        raise ArkError("参考图必须是 Base64 data URL，不能传 MinIO / localhost 地址")
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": user},
            ],
        },
    ]
    return _post_chat(messages, max_tokens=max_tokens)


def download_image(tos_url: str) -> tuple[bytes, str]:
    """必须 GET。HEAD 实测 403。"""
    try:
        with httpx.Client(timeout=ARK_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(tos_url)
    except httpx.HTTPError as exc:
        raise ArkError(f"下载 TOS 图片失败：{exc}") from exc
    if resp.status_code >= 400:
        raise ArkError(f"下载 TOS 图片 HTTP {resp.status_code}")
    content_type = resp.headers.get("content-type", "image/png").split(";")[0].strip()
    if not resp.content:
        raise ArkError("TOS 图片为空")
    return resp.content, content_type or "image/png"


def create_i2v_task(
    prompt: str,
    image_data_url: str,
    *,
    duration: int = 5,
    resolution: str = "480p",
    ratio: str = "16:9",
) -> str:
    """提交图生视频任务，返回方舟 task id。图必须是 Base64 data URL。"""
    if not image_data_url.startswith("data:image/"):
        raise ArkError("参考图必须是 Base64 data URL，不能传 MinIO / localhost 地址")
    if duration not in ALLOWED_VIDEO_DURATIONS:
        raise ArkError(f"演示仅开放 {sorted(ALLOWED_VIDEO_DURATIONS)} 秒")
    if resolution not in ALLOWED_VIDEO_RESOLUTIONS:
        raise ArkError(f"未开放的分辨率：{resolution}")
    if not settings.ARK_API_KEY or not settings.ARK_VIDEO_ENDPOINT:
        raise ArkError("未配置 ARK_API_KEY / ARK_VIDEO_ENDPOINT")
    text = f"{prompt.strip()} --rs {resolution} --dur {duration} --rt {ratio}"
    payload = {
        "model": settings.ARK_VIDEO_ENDPOINT,
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ],
    }
    url = f"{settings.ARK_BASE_URL.rstrip('/')}/contents/generations/tasks"
    headers = {
        "Authorization": f"Bearer {settings.ARK_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=VIDEO_TIMEOUT, follow_redirects=True) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise ArkError(f"调用方舟网络失败：{exc}") from exc
    if resp.status_code >= 400:
        raise ArkError(_extract_error(resp), status_code=resp.status_code)
    try:
        body = resp.json()
    except ValueError as exc:
        raise ArkError("方舟返回非 JSON") from exc
    task_id = body.get("id")
    if not task_id:
        raise ArkError("方舟未返回视频任务 ID")
    return str(task_id)


def get_i2v_task(task_id: str) -> dict[str, Any]:
    """查询图生视频任务状态。"""
    if not settings.ARK_API_KEY:
        raise ArkError("未配置 ARK_API_KEY")
    url = f"{settings.ARK_BASE_URL.rstrip('/')}/contents/generations/tasks/{task_id}"
    headers = {"Authorization": f"Bearer {settings.ARK_API_KEY}"}
    try:
        with httpx.Client(timeout=VIDEO_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ArkError(f"查询视频任务失败：{exc}") from exc
    if resp.status_code >= 400:
        raise ArkError(_extract_error(resp), status_code=resp.status_code)
    try:
        body = resp.json()
    except ValueError as exc:
        raise ArkError("方舟返回非 JSON") from exc
    if not isinstance(body, dict):
        raise ArkError("方舟返回不是对象")
    return body


def wait_i2v_result(
    task_id: str,
    *,
    on_tick: Callable[[], None] | None = None,
) -> str:
    """轮询直到成功，返回视频 URL。超时或失败抛 ArkError。"""
    deadline = time.monotonic() + VIDEO_POLL_TIMEOUT
    last_status = ""
    while time.monotonic() < deadline:
        body = get_i2v_task(task_id)
        if on_tick is not None:
            on_tick()
        last_status = str(body.get("status") or "")
        if last_status == "succeeded":
            return extract_video_url(body)
        if last_status in {"failed", "cancelled", "canceled", "expired"}:
            raise ArkError(_video_error_message(body, last_status))
        time.sleep(VIDEO_POLL_INTERVAL)
    raise ArkError(f"视频生成超时（最后状态：{last_status or '未知'}）")


def extract_video_url(body: dict[str, Any]) -> str:
    """兼容 content.video_url / content[].video_url / output.video_url。"""
    candidates: list[Any] = [
        body.get("video_url"),
        (body.get("content") or {}).get("video_url") if isinstance(body.get("content"), dict) else None,
        (body.get("output") or {}).get("video_url") if isinstance(body.get("output"), dict) else None,
    ]
    content = body.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            candidates.append(item.get("video_url"))
            video = item.get("video")
            if isinstance(video, dict):
                candidates.append(video.get("url"))
    for url in candidates:
        if isinstance(url, str) and url.startswith("http"):
            return url
    raise ArkError("方舟未返回视频 URL")


def _video_error_message(body: dict[str, Any], status: str) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        msg = err.get("message") or err.get("code")
        if msg:
            return f"视频生成失败：{msg}"
    if isinstance(err, str) and err:
        return f"视频生成失败：{err}"
    reason = body.get("failure_reason") or body.get("message")
    if isinstance(reason, str) and reason:
        return f"视频生成失败：{reason}"
    return f"视频生成失败：{status}"


def _base_payload(prompt: str, size: str) -> dict[str, Any]:
    if size not in ALLOWED_SIZES:
        raise ArkError(f"未开放的尺寸：{size}，仅支持 {sorted(ALLOWED_SIZES)}")
    if not settings.ARK_API_KEY or not settings.ARK_IMAGE_ENDPOINT:
        raise ArkError("未配置 ARK_API_KEY / ARK_IMAGE_ENDPOINT")
    return {
        "model": settings.ARK_IMAGE_ENDPOINT,
        "prompt": prompt,
        "size": size,
        "response_format": "url",
        "watermark": False,
        "output_format": "png",
    }


def _post_generations(payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{settings.ARK_BASE_URL.rstrip('/')}/images/generations"
    headers = {
        "Authorization": f"Bearer {settings.ARK_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=ARK_TIMEOUT, follow_redirects=True) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise ArkError(f"调用方舟网络失败：{exc}") from exc

    if resp.status_code >= 400:
        message = _extract_error(resp)
        raise ArkError(message, status_code=resp.status_code)

    try:
        body = resp.json()
    except ValueError as exc:
        raise ArkError("方舟返回非 JSON") from exc

    data = body.get("data") or []
    if not data or not data[0].get("url"):
        raise ArkError("方舟未返回图片 URL")
    return body


def _post_chat(messages: list[dict[str, Any]], *, max_tokens: int) -> str:
    if not settings.ARK_API_KEY:
        raise ArkError("未配置 ARK_API_KEY")
    url = f"{settings.ARK_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.ARK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": ARK_CHAT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }
    try:
        with httpx.Client(timeout=CHAT_TIMEOUT, follow_redirects=True) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise ArkError(f"调用方舟网络失败：{exc}") from exc

    if resp.status_code != 200:
        raise ArkError(_extract_error(resp), status_code=resp.status_code)

    try:
        body = resp.json()
    except ValueError as exc:
        raise ArkError("方舟返回非 JSON") from exc

    return _normalize_prompt(_extract_chat_text(body))


def _extract_chat_text(body: dict[str, Any]) -> str:
    choices = body.get("choices") or []
    if not choices or not isinstance(choices, list):
        raise ArkError("方舟未返回文本")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in (None, "text") and item.get("text"):
                    parts.append(str(item["text"]))
        text = "".join(parts).strip()
    else:
        text = ""
    if not text:
        raise ArkError("方舟未返回文本")
    return text


def _normalize_prompt(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t[3:]
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1 :]
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
    if len(t) >= 2 and ((t[0] == t[-1] == '"') or (t[0] == "“" and t[-1] == "”")):
        t = t[1:-1].strip()
    if not t:
        raise ArkError("方舟未返回文本")
    return t


def _extract_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error") or {}
        code = err.get("code") or ""
        msg = err.get("message") or resp.text
        return f"方舟错误 {resp.status_code} {code}: {msg}".strip()
    except ValueError:
        return f"方舟 HTTP {resp.status_code}: {resp.text[:500]}"
