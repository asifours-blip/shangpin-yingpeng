"""文案生成入口。旧 HTTP 接口和流水线 Worker 都走这里，不自己再调 HTTP。"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.schemas.copywriting import GeneratedContent, RiskResult
from app.services.ark import ArkError, chat_vision
from app.services.copy_templates import build_copy_prompts
from app.services.risk_lexicon import check_content

_SECRET_RE = re.compile(r"(?i)(?:bearer\s+[a-z0-9._\-]+|sk-[a-z0-9]+)")


def sanitize_error(exc: BaseException) -> str:
    if isinstance(exc, ValidationError):
        msg = "模型返回结构不符合文案 schema"
    elif isinstance(exc, ArkError):
        msg = exc.message
    else:
        msg = str(exc) or exc.__class__.__name__
    msg = _SECRET_RE.sub("[已脱敏]", msg)
    msg = msg.replace("\n", " ").strip()
    if len(msg) > 300:
        msg = msg[:300] + "…"
    return msg or "文案生成失败"


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = stripped[3:]
    if stripped.lower().startswith("json"):
        stripped = stripped[4:]
    stripped = stripped.strip()
    if stripped.endswith("```"):
        stripped = stripped[:-3].strip()
    return stripped


def _relax_json(text: str) -> str:
    relaxed = (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )
    return re.sub(r",\s*([}\]])", r"\1", relaxed)


def _loads_dict(text: str) -> dict | None:
    for candidate in (text, _relax_json(text)):
        try:
            data = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def extract_json_object(raw: str) -> dict:
    text = _strip_fence(raw or "")
    if not text:
        raise ValueError("模型返回为空")
    data = _loads_dict(text)
    if data is not None:
        return data
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        data = _loads_dict(text[start : end + 1])
        if data is not None:
            return data
    raise ValueError("模型返回不是合法 JSON 对象")


def parse_generated(raw: str) -> GeneratedContent:
    return GeneratedContent.model_validate(extract_json_object(raw))


def produce_copy(
    *, platform: str, facts: dict, data_url: str, generation_requirements: str = "",
    source_references: list[dict] | None = None,
) -> tuple[GeneratedContent, RiskResult]:
    """给定图片 data URL 和商品事实，生成并扫描一篇平台文案。"""
    system, user_text = build_copy_prompts(
        platform, facts, generation_requirements=generation_requirements,
        source_references=source_references,
    )
    model_text = chat_vision(system, user_text, data_url, max_tokens=4096)
    content = parse_generated(model_text)
    return content, check_content(content)
