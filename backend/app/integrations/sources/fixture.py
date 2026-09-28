"""内部开发样本。只在非生产环境可用，不代表任何平台热榜。"""

from app.core.config import settings
from app.integrations.sources.base import (
    CapabilityUnavailable,
    CollectionQuery,
    FixtureDisabled,
    SourceItemIn,
    SourcePage,
)

FIXTURE_SCOPE = (
    "内部开发数据：箱包类目商品样本，按样本序号排序。"
    "不是抖音、小红书或淘宝的全平台爆款前 100。"
)
FIXTURE_VERSION = "fixture-bags-v1"
PAGE_SIZE = 40


def build_fixture_items() -> list[SourceItemIn]:
    kinds = ("托特", "双肩", "斜挎", "钱包", "旅行袋")
    colors = ("黑", "棕", "米", "海军蓝")
    items: list[SourceItemIn] = []
    for index in range(1, 101):
        kind = kinds[(index - 1) % len(kinds)]
        color = colors[(index - 1) % len(colors)]
        items.append(
            SourceItemIn(
                platform="fixture",
                item_kind="product",
                external_id=f"bag-{index:03d}",
                url=f"https://fixture.internal/bags/{index:03d}",
                title=f"{color}{kind}样本 {index:03d}",
                text_excerpt=None,
                media_refs=[],
                raw_metrics={
                    "dataset": "internal_fixture",
                    "sort_metric": "sample_order",
                    "sample_order": index,
                },
                source_rank=index,
                rights_scope="internal_dev_only",
            )
        )
    return items


class FixtureSourceAdapter:
    provider = "fixture"

    def capabilities(self) -> set[str]:
        if not settings.allow_fixture_sources:
            return set()
        return {"product_search"}

    def ensure_ready(self, config: CollectionQuery) -> None:
        if not settings.allow_fixture_sources:
            raise FixtureDisabled("生产环境拒绝内部测试数据源")
        if config.item_kind != "product":
            raise CapabilityUnavailable("内部样本只有商品，没有笔记或短视频文案")
        if "product_search" not in self.capabilities():
            raise CapabilityUnavailable("内部样本不可用")

    def fetch_page(self, config: CollectionQuery, cursor: str | None) -> SourcePage:
        self.ensure_ready(config)
        catalog = build_fixture_items()
        start = int(cursor or "0")
        chunk = catalog[start : start + PAGE_SIZE]
        next_index = start + PAGE_SIZE
        next_cursor = str(next_index) if next_index < len(catalog) else None
        return SourcePage(
            items=chunk,
            next_cursor=next_cursor,
            scope_description=FIXTURE_SCOPE,
            provider_response_version=FIXTURE_VERSION,
        )
