"""按 provider 取适配器。fixture 在生产配置下不可实例化成数据源。"""

from app.integrations.sources.base import CapabilityUnavailable
from app.integrations.sources.douyin import DouyinContentAdapter
from app.integrations.sources.fixture import FixtureSourceAdapter
from app.integrations.sources.taobao import TaobaoMaterialAdapter
from app.integrations.sources.xiaohongshu import XiaohongshuContentAdapter

_FACTORIES = {
    "fixture": FixtureSourceAdapter,
    "taobao": TaobaoMaterialAdapter,
    "douyin": DouyinContentAdapter,
    "xiaohongshu": XiaohongshuContentAdapter,
}


def get_adapter(provider: str):
    factory = _FACTORIES.get(provider)
    if factory is None:
        raise CapabilityUnavailable(f"不支持的数据源：{provider}")
    return factory()
