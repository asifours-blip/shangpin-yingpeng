"""抖音内容采集。未获批官方查询能力时不返回数据。"""

from app.integrations.sources.base import (
    CapabilityUnavailable,
    CollectionQuery,
    SourcePage,
)

DOUYIN_UNAVAILABLE = "待连接：抖音内容采集需要已获批的官方接口，当前没有可用数据"


class DouyinContentAdapter:
    provider = "douyin"

    def capabilities(self) -> set[str]:
        return set()

    def ensure_ready(self, config: CollectionQuery) -> None:
        del config
        raise CapabilityUnavailable(DOUYIN_UNAVAILABLE)

    def fetch_page(self, config: CollectionQuery, cursor: str | None) -> SourcePage:
        del cursor
        self.ensure_ready(config)
        raise CapabilityUnavailable(DOUYIN_UNAVAILABLE)
