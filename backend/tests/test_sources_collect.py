"""采集去重、如实计数、无凭证不编造。不连 PostgreSQL。"""

from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.integrations.sources.base import (
    CapabilityUnavailable,
    CollectionQuery,
    CredentialsMissing,
    FixtureDisabled,
    SourceItemIn,
    SourcePage,
)
from app.integrations.sources.douyin import DouyinContentAdapter
from app.integrations.sources.fixture import FIXTURE_SCOPE, FixtureSourceAdapter
from app.integrations.sources.taobao import TaobaoMaterialAdapter, map_tbk_row, top_sign
from app.integrations.sources.xiaohongshu import XiaohongshuContentAdapter
from app.services.collect import accumulate


def _page(items, *, cursor=None, scope="箱包商品样本", version="test"):
    return SourcePage(
        items=items,
        next_cursor=cursor,
        scope_description=scope,
        provider_response_version=version,
    )


def _item(external_id, *, platform="fixture", kind="product", title="托特包", rank=None, excerpt=None):
    return SourceItemIn(
        platform=platform,
        item_kind=kind,
        external_id=external_id,
        url=f"https://fixture.internal/bags/{external_id}",
        title=title,
        text_excerpt=excerpt,
        source_rank=rank,
        raw_metrics={"dataset": platform},
    )


def test_caps_at_100_and_drops_duplicates_and_blanks():
    page = _page(
        [
            _item("bag-001"),
            _item("bag-001"),
            SourceItemIn(
                platform="fixture",
                item_kind="product",
                external_id=" ",
                url=None,
                title=" ",
            ),
            _item("bag-002", title=""),
            _item("bag-003"),
        ]
    )
    result = accumulate(lambda _cursor: page, max_items=100)
    assert result.status == "succeeded"
    assert [item.external_id for item in result.items] == ["bag-001", "bag-003"]
    assert result.dropped_duplicate == 1
    assert result.dropped_invalid == 2
    assert result.actual_count == 2


def test_seventy_three_is_not_padded():
    items = [_item(f"bag-{i:03d}") for i in range(1, 74)]
    result = accumulate(lambda _cursor: _page(items, scope=FIXTURE_SCOPE), max_items=100)
    assert result.status == "succeeded"
    assert result.actual_count == 73
    assert "全平台爆款前 100" not in result.scope_description or "不是" in result.scope_description


def test_page_break_is_partial_not_success():
    def fetch(cursor):
        if cursor is None:
            return _page([_item(f"bag-{i:03d}") for i in range(1, 11)], cursor="2")
        raise CredentialsMissing("分页中断")

    result = accumulate(fetch, max_items=100)
    assert result.status == "partial"
    assert result.actual_count == 10
    assert result.error_code == "credentials_missing"


def test_fixture_catalog_is_100_unique_products(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    adapter = FixtureSourceAdapter()
    query = CollectionQuery(provider="fixture", item_kind="product", query="箱包", max_items=100)
    result = accumulate(lambda cursor: adapter.fetch_page(query, cursor), max_items=100)
    ids = [item.external_id for item in result.items]
    assert result.status == "succeeded"
    assert len(ids) == 100
    assert len(set(ids)) == 100
    assert all(item.item_kind == "product" for item in result.items)
    assert all(item.text_excerpt is None for item in result.items)
    assert "不是抖音、小红书或淘宝的全平台爆款前 100" in result.scope_description


def test_rerun_does_not_add_new_identities(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    adapter = FixtureSourceAdapter()
    query = CollectionQuery(provider="fixture", item_kind="product", max_items=100)

    def run():
        return accumulate(lambda cursor: adapter.fetch_page(query, cursor), max_items=100)

    first = run()
    second = run()
    first_keys = {(item.platform, item.item_kind, item.external_id) for item in first.items}
    added = [item for item in second.items if (item.platform, item.item_kind, item.external_id) not in first_keys]
    assert added == []
    assert len(first.items) == len(second.items) == 100


def test_production_rejects_fixture(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    adapter = FixtureSourceAdapter()
    query = CollectionQuery(provider="fixture", item_kind="product")
    with pytest.raises(FixtureDisabled):
        adapter.ensure_ready(query)
    assert adapter.capabilities() == set()


def test_missing_taobao_credentials_do_not_invent_products(monkeypatch):
    monkeypatch.setattr(settings, "TAOBAO_APP_KEY", "")
    monkeypatch.setattr(settings, "TAOBAO_APP_SECRET", "")
    monkeypatch.setattr(settings, "TAOBAO_ADZONE_ID", "")
    adapter = TaobaoMaterialAdapter()
    assert "product_search" not in adapter.capabilities()
    with pytest.raises(CredentialsMissing, match="不会生成虚构商品"):
        adapter.ensure_ready(CollectionQuery(provider="taobao", item_kind="product", query="箱包"))


def test_tbk_row_stays_a_product_listing():
    mapped = map_tbk_row(
        {
            "item_id": "123",
            "title": "黑色托特",
            "item_url": "https://item.taobao.com/item.htm?id=123",
            "volume": 20,
            "pict_url": "https://img.example/a.jpg",
        },
        rank=1,
    )
    assert mapped is not None
    assert mapped.item_kind == "product"
    assert mapped.text_excerpt is None
    assert mapped.title == "黑色托特"
    assert mapped.raw_metrics["dataset"] == "taobao.tbk.dg.material.optional"


def test_tbk_does_not_treat_posts_as_supported():
    adapter = TaobaoMaterialAdapter()
    with pytest.raises(CapabilityUnavailable, match="不提供笔记正文"):
        adapter.ensure_ready(CollectionQuery(provider="taobao", item_kind="post"))


def test_douyin_and_xhs_have_no_fabricated_posts():
    query = CollectionQuery(provider="douyin", item_kind="post", query="箱包")
    with pytest.raises(CapabilityUnavailable, match="没有可用数据"):
        DouyinContentAdapter().ensure_ready(query)
    with pytest.raises(CapabilityUnavailable, match="不会编造热门文案"):
        XiaohongshuContentAdapter().ensure_ready(
            CollectionQuery(provider="xiaohongshu", item_kind="post")
        )


def test_sort_metrics_are_not_added_across_datasets(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    fixture = accumulate(
        lambda cursor: FixtureSourceAdapter().fetch_page(
            CollectionQuery(provider="fixture", item_kind="product", max_items=5),
            cursor,
        ),
        max_items=5,
    )
    other = accumulate(
        lambda _cursor: _page(
            [_item("dy-1", platform="douyin", kind="post", title="笔记", excerpt="开头")],
            scope="抖音关键词搜索，不是销量",
        ),
        max_items=5,
    )
    assert fixture.items[0].raw_metrics["dataset"] == "internal_fixture"
    assert other.items[0].raw_metrics["dataset"] == "douyin"
    assert fixture.items[0].source_rank == 1
    assert other.items[0].source_rank == 1


def test_top_sign_is_stable():
    signed = top_sign({"method": "taobao.tbk.dg.material.optional", "app_key": "a"}, "secret")
    assert signed == top_sign({"app_key": "a", "method": "taobao.tbk.dg.material.optional"}, "secret")
    assert signed != top_sign({"method": "taobao.tbk.dg.material.optional", "app_key": "a"}, "other")
    assert signed.isupper()


def test_url_without_id_hashes_stable():
    from app.services.collect import stable_external_id

    first = stable_external_id(None, "HTTPS://Item.Example/a?id=1#frag")
    second = stable_external_id("", "https://item.example/a?id=1")
    assert first == second
    assert first is not None
    observed = datetime.now(timezone.utc)
    assert observed.tzinfo is not None
