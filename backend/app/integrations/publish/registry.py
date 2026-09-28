"""按平台取适配器。测试替身只在测试里替换，不进产品默认路径。"""

from __future__ import annotations

from app.integrations.publish.base import PublishAdapter
from app.integrations.publish.douyin import DouyinPublishAdapter
from app.integrations.publish.xiaohongshu import XiaohongshuPublishAdapter

_ADAPTERS: dict[str, PublishAdapter] = {
    "douyin": DouyinPublishAdapter(),
    "xiaohongshu": XiaohongshuPublishAdapter(),
}


def get_adapter(platform: str) -> PublishAdapter:
    adapter = _ADAPTERS.get(platform)
    if adapter is None:
        raise KeyError(platform)
    return adapter


def catalog() -> list[dict]:
    return [item.catalog() for item in _ADAPTERS.values()]
