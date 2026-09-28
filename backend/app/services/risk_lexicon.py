"""预设风险词表：命中是检查线索，不是违规判决。"""

from app.schemas.copywriting import GeneratedContent, RiskHit, RiskResult

# 词条至少两字或含符号。禁止把单字「最」当违规。
_RISK_TERMS: tuple[tuple[str, str], ...] = (
    ("第一", "可能被理解为排名或比较级绝对化表述"),
    ("国家级", "可能被理解为官方或监管背书"),
    ("根治", "可能被理解为疗效承诺"),
    ("100%", "可能被理解为绝对化保证"),
    ("百分之百", "可能被理解为绝对化保证"),
    ("永久", "可能被理解为无法核实的时效承诺"),
    ("全网最低", "可能被理解为价格绝对化"),
    ("销量第一", "可能被理解为排名绝对化"),
    ("无效退款", "可能被理解为效果保证"),
    ("无副作用", "可能被理解为安全承诺"),
    ("立即见效", "可能被理解为功效时效承诺"),
    ("立刻见效", "可能被理解为功效时效承诺"),
    ("纯天然", "可能被理解为无法证实的成分主张"),
    ("祖传", "可能被理解为夸大来源"),
    ("特效", "可能被理解为功效承诺"),
    ("速效", "可能被理解为功效承诺"),
    ("免检", "可能被理解为监管背书"),
    ("包治", "可能被理解为疗效承诺"),
    ("最好", "可能被理解为比较级绝对化"),
    ("最佳", "可能被理解为比较级绝对化"),
    ("最便宜", "可能被理解为价格绝对化"),
    ("最强", "可能被理解为比较级绝对化"),
    ("独一无二", "可能被理解为无法核实的唯一性宣称"),
    ("万能", "可能被理解为夸大功能"),
    ("绝对", "可能被理解为绝对化保证"),
)

_SUGGESTION = "请人工核对：改为可核实的体验描述，或删除该表述。命中只是检查线索。"

_HIT_SUMMARY = "发现待检查项"
_CLEAN_SUMMARY = "未发现预设规则命中"


def check_text(text: str) -> RiskResult:
    """扫描纯文本。单字「最」不会入表，也不会单独命中。"""
    hay = text or ""
    hits: list[RiskHit] = []
    seen: set[str] = set()
    for fragment, reason in _RISK_TERMS:
        if fragment == "最":
            continue
        if fragment and fragment not in seen and fragment in hay:
            seen.add(fragment)
            hits.append(
                RiskHit(fragment=fragment, reason=reason, suggestion=_SUGGESTION)
            )
    return RiskResult(
        hits=hits,
        summary=_HIT_SUMMARY if hits else _CLEAN_SUMMARY,
        need_human_review=bool(hits),
    )


def check_content(content: GeneratedContent) -> RiskResult:
    """拼接标题、正文、话题后检查。"""
    parts = [content.title, content.body, *content.hashtags]
    return check_text("\n".join(parts))
