"""排版前的消费者文案质检。不改原文，不把核查语塞进画面。"""

from __future__ import annotations

import re

INTERNAL_PHRASES = (
    "未确认",
    "待核查",
    "待确认",
    "不能写成卖点",
    "不能代替联调",
    "不是方舟",
    "方舟原生",
    "内部核查",
)

# 没有事实支撑时，这些词不能出现在要上画面的正文里。
_UNSUPPORTED_TOKENS = ("容量", "尺寸", "承重", "厘米", "公斤", "体验", "好用", "通勤", "上班", "能装")
_LABEL_FACT = {"容量": "capacity", "尺寸": "size", "承重": "load"}

_NEGATIVE_WATERPROOF = {"no", "denied", "not_waterproof", False}

# 自动重写次数。第一次草稿之外最多再生成这么多次，并且每次都要占预算。
COPY_REWRITE_LIMIT = 1

_CONTENT_FIELDS = (
    ("size", "尺寸"),
    ("capacity", "容量"),
    ("load", "承重"),
)


def _waterproof_issue(text: str, facts: dict) -> dict | None:
    """有依据的「不防水」不能因为里面带着「防水」两个字被拦下。"""
    if not text or "防水" not in text:
        return None
    status = facts.get("waterproof")
    positive = re.search(r"(?<!不)防水", text) is not None
    negative = "不防水" in text
    if status == "confirmed":
        return None
    if negative and not positive and status in _NEGATIVE_WATERPROOF:
        return None
    if negative and not positive:
        return {
            "code": "unconfirmed_fact",
            "reason": "事实没有证明不防水，不能把否定句当成已经核实的卖点",
        }
    return {
        "code": "unconfirmed_fact",
        "reason": "防水没有确认，不能写成卖点",
    }


def _internal_issues(text: str) -> list[dict]:
    found = []
    for phrase in INTERNAL_PHRASES:
        if phrase in text:
            found.append(
                {
                    "code": "internal_wording",
                    "reason": f"含内部核查措辞「{phrase}」，不能删词后继续用",
                    "phrase": phrase,
                }
            )
    return found


def _ungrounded_issues(text: str, facts: dict) -> list[dict]:
    issues = []
    blob = " ".join(str(facts.get(key) or "") for key in ("product_name", "material", "size", "capacity", "load", "scene"))
    scene_ok = bool(facts.get("scene") and facts.get("scene_evidence"))
    for token in _UNSUPPORTED_TOKENS:
        if token not in text:
            continue
        if token in blob:
            continue
        fact_key = _LABEL_FACT.get(token)
        if fact_key and facts.get(fact_key):
            continue
        if token in {"通勤", "上班"} and scene_ok and token in str(facts.get("scene")):
            continue
        issues.append(
            {
                "code": "ungrounded",
                "reason": f"「{token}」没有锁定事实或场景依据，不能写进消费者画面",
                "phrase": token,
            }
        )
    return issues


def screen_text(text: str, facts: dict, *, field: str, check_grounding: bool = True) -> list[dict]:
    """返回问题列表。原文保持不动。"""
    raw = text or ""
    if not raw.strip():
        return []
    issues = []
    for item in _internal_issues(raw):
        issues.append({**item, "field": field, "original": raw})
    waterproof = _waterproof_issue(raw, facts)
    if waterproof is not None:
        issues.append({**waterproof, "field": field, "original": raw})
    if check_grounding:
        for item in _ungrounded_issues(raw, facts):
            issues.append({**item, "field": field, "original": raw})
    return issues


def screen_consumer_copy(
    *,
    title: str,
    body: str,
    hashtags: list[str] | None,
    facts: dict,
    cards: list[dict] | None = None,
) -> dict:
    """标题、正文、话题和卡片文案进排版前一起查。

    标题、话题、卡片标题里如果写出了功能、规格或适用场景，要有锁定事实。
    普通话题（例如「箱包」）不含这些用词时，不要求另附证据。
    """
    issues: list[dict] = []
    issues.extend(screen_text(title, facts, field="title", check_grounding=True))
    issues.extend(screen_text(body, facts, field="body", check_grounding=True))
    for tag in hashtags or []:
        issues.extend(screen_text(str(tag), facts, field="hashtag", check_grounding=True))
    for index, card in enumerate(cards or []):
        issues.extend(screen_text(str(card.get("title") or ""), facts, field=f"card:{index}:title", check_grounding=True))
        issues.extend(screen_text(str(card.get("body") or ""), facts, field=f"card:{index}:body", check_grounding=True))
    return {"ok": not issues, "issues": issues}


def _only_identity(text: str, facts: dict) -> bool:
    """正文如果只是在重复商品名和材质，封面就不要再写一遍。"""
    residual = text
    for chunk in (facts.get("product_name"), facts.get("material"), "材质"):
        if chunk:
            residual = residual.replace(str(chunk), "")
    residual = re.sub(r"[，。、,.\s是的为]", "", residual)
    return residual == ""


def _fact_card(purpose: str, title: str, body: str, cites: list[str]) -> dict:
    return {
        "role": "card",
        "purpose": purpose,
        "title": title,
        "body": body,
        "cites": cites,
    }


def _optional_supplements(facts: dict) -> list[str]:
    """还缺内容卡时可以补的资料。不是每一项都必填。未知防水不列入、也不使用。"""
    options: list[str] = []
    if not facts.get("material"):
        options.append("材质")
    for _key, label in _CONTENT_FIELDS:
        if not facts.get(_key):
            options.append(label)
    if not (facts.get("scene") and facts.get("scene_evidence")):
        options.append("有依据的使用场景")
    return options


def plan_xiaohongshu(facts: dict, *, title: str = "", body: str = "") -> dict:
    """封面加一至四张有事实依据的内容卡；无实质事实才阻塞。"""
    name = str(facts.get("product_name") or title or "").strip()
    cards: list[dict] = []
    if name:
        material = str(facts.get("material") or "")
        cover_body = ""
        if body and not screen_text(body, facts, field="body", check_grounding=True):
            candidate = body.strip()
            if not _only_identity(candidate, facts):
                cover_body = candidate
        cites = ["product_name"]
        if material and material in cover_body:
            cites.append("material")
        cards.append(
            {
                "role": "cover",
                "purpose": "识别商品",
                "title": name,
                "body": cover_body,
                "cites": cites,
            }
        )
    material = facts.get("material")
    if material:
        cards.append(_fact_card("材质", "材质", f"材质：{material}", ["material"]))
    for key, label in _CONTENT_FIELDS:
        value = facts.get(key)
        if value:
            cards.append(_fact_card(label, label, f"{label}：{value}", [key]))
    scene = facts.get("scene")
    if scene and facts.get("scene_evidence"):
        cards.append(_fact_card("使用场景", "使用场景", str(scene), ["scene", "scene_evidence"]))
    content = [card for card in cards if card["role"] == "card"]
    chosen: list[dict] = []
    seen: set[str] = set()
    for card in content:
        if card["purpose"] in seen:
            continue
        seen.add(card["purpose"])
        chosen.append(card)
        if len(chosen) == 4:
            break
    if facts.get("waterproof") == "confirmed" and "防水" not in seen and len(chosen) < 4:
        chosen.append(_fact_card("防水", "防水", "防水", ["waterproof"]))
    elif facts.get("waterproof") in _NEGATIVE_WATERPROOF and "防水" not in seen and len(chosen) < 4:
        chosen.append(_fact_card("防水", "防水", "不防水", ["waterproof"]))
    cover = [card for card in cards if card["role"] == "cover"]
    kept = cover + chosen
    enough = bool(chosen and cover)
    supplements = [] if enough else _optional_supplements(facts)
    if not name and not enough:
        supplements = ["商品名", *supplements]
    return {
        "code": None if enough else "content_insufficient",
        "missing": supplements,
        "supplements_required": False,
        "need_more_cards": 0 if enough else 1,
        "cards": kept,
        "rejected_body": body if body and screen_text(body, facts, field="body") else "",
    }
