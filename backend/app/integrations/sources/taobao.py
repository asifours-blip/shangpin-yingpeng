"""淘宝客物料搜索。只映射商品字段，不把标题写成笔记正文。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.integrations.sources.base import (
    CapabilityUnavailable,
    CollectionQuery,
    CredentialsMissing,
    SourceItemIn,
    SourcePage,
)

TBK_METHOD = "taobao.tbk.dg.material.optional"
TBK_GATEWAY = "https://eco.taobao.com/router/rest"
TBK_SORTS = {
    "total_sales",
    "tk_total_sales",
    "tk_rate_des",
    "tk_rate_asc",
    "tk_total_commi",
    "price",
    "match",
}


def top_sign(params: dict[str, str], secret: str) -> str:
    pieces = secret + "".join(f"{key}{params[key]}" for key in sorted(params)) + secret
    return hashlib.md5(pieces.encode("utf-8")).hexdigest().upper()


def map_tbk_row(row: dict[str, Any], *, rank: int) -> SourceItemIn | None:
    external_id = str(row.get("item_id") or row.get("num_iid") or "").strip()
    title = row.get("title") or row.get("short_title")
    if not external_id or not title:
        return None
    description = row.get("item_description")
    excerpt = description.strip() if isinstance(description, str) and description.strip() else None
    images: list[str] = []
    pict = row.get("pict_url") or row.get("white_image")
    if isinstance(pict, str) and pict:
        images.append(pict)
    metrics = {
        "dataset": "taobao.tbk.dg.material.optional",
        "volume": row.get("volume"),
        "tk_total_sales": row.get("tk_total_sales"),
        "zk_final_price": row.get("zk_final_price"),
    }
    return SourceItemIn(
        platform="taobao",
        item_kind="product",
        external_id=external_id,
        url=row.get("item_url") or row.get("url"),
        title=str(title),
        text_excerpt=excerpt,
        media_refs=images,
        raw_metrics={key: value for key, value in metrics.items() if value is not None},
        source_rank=rank,
        raw_payload=row,
        rights_scope="taobao_affiliate_listing",
    )


def _rows_from_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    if "error_response" in payload:
        err = payload["error_response"]
        message = err.get("sub_msg") or err.get("msg") or "淘宝客接口返回错误"
        raise CapabilityUnavailable(str(message))
    body = payload.get("tbk_dg_material_optional_response") or {}
    result = body.get("result_list") or {}
    rows = result.get("map_data") or []
    if isinstance(rows, dict):
        rows = [rows]
    total = body.get("total_results")
    return list(rows), int(total) if total is not None else None


class TaobaoMaterialAdapter:
    provider = "taobao"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def _configured(self) -> bool:
        return bool(settings.TAOBAO_APP_KEY and settings.TAOBAO_APP_SECRET and settings.TAOBAO_ADZONE_ID)

    def capabilities(self) -> set[str]:
        if not self._configured():
            return set()
        return {"product_search"}

    def ensure_ready(self, config: CollectionQuery) -> None:
        if config.item_kind != "product":
            raise CapabilityUnavailable("淘宝客物料搜索只提供商品，不提供笔记正文")
        if not self._configured():
            raise CredentialsMissing("待连接：未配置淘宝客 AppKey 与推广位，不会生成虚构商品")

    def fetch_page(self, config: CollectionQuery, cursor: str | None) -> SourcePage:
        self.ensure_ready(config)
        page_no, page_size = _page_from_cursor(cursor, config.max_items)
        sort = config.sort_metric if config.sort_metric in TBK_SORTS else "total_sales"
        params: dict[str, str] = {
            "method": TBK_METHOD,
            "app_key": settings.TAOBAO_APP_KEY,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            "adzone_id": settings.TAOBAO_ADZONE_ID,
            "q": config.query or config.category or "箱包",
            "page_no": str(page_no),
            "page_size": str(page_size),
            "sort": sort,
        }
        if config.category:
            params["cat"] = config.category
        params["sign"] = top_sign(params, settings.TAOBAO_APP_SECRET)
        client = self._client or httpx.Client(timeout=20.0)
        close = self._client is None
        try:
            response = client.post(TBK_GATEWAY, data=params)
            response.raise_for_status()
            payload = response.json()
        finally:
            if close:
                client.close()
        rows, _total = _rows_from_payload(payload)
        start_rank = (page_no - 1) * page_size
        items = []
        for offset, row in enumerate(rows, start=1):
            mapped = map_tbk_row(row, rank=start_rank + offset)
            if mapped is not None:
                items.append(mapped)
        next_cursor = None
        if rows and len(rows) >= page_size:
            next_cursor = f"{page_no + 1}:{page_size}"
        return SourcePage(
            items=items,
            next_cursor=next_cursor,
            scope_description=(
                "淘宝客物料搜索：关键词/类目下的推广商品，按所选排序字段。"
                "不是社交平台笔记，也不是全网销量榜。"
            ),
            provider_response_version=TBK_METHOD,
        )


def _page_from_cursor(cursor: str | None, max_items: int) -> tuple[int, int]:
    page_size = min(20, max_items)
    if not cursor:
        return 1, page_size
    page_text, _, size_text = cursor.partition(":")
    page_no = int(page_text or "1")
    parsed_size = int(size_text or page_size)
    return page_no, min(parsed_size, 100)
