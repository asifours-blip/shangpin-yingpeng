"""小红书内容采集。未核实官方笔记搜索接口前不返回数据。"""

from app.integrations.sources.base import (
    CapabilityUnavailable,
    CollectionQuery,
    SourcePage,
)

XHS_UNAVAILABLE = "待连接：尚未核实本账号可用的小红书笔记搜索官方接口，不会编造热门文案"


class XiaohongshuContentAdapter:
    provider = "xiaohongshu"

    def capabilities(self) -> set[str]:
        return set()

    def ensure_ready(self, config: CollectionQuery) -> None:
        del config
        raise CapabilityUnavailable(XHS_UNAVAILABLE)

    def fetch_page(self, config: CollectionQuery, cursor: str | None) -> SourcePage:
        del cursor
        self.ensure_ready(config)
        raise CapabilityUnavailable(XHS_UNAVAILABLE)
