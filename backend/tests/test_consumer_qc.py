"""消费者文案质检。不改原文，事实不够就阻塞。"""

from app.services.consumer_qc import plan_xiaohongshu, screen_consumer_copy, screen_text


def test_internal_wording_is_kept_not_stripped():
    original = "帆布包适合通勤，没有未确认的功能"
    issues = screen_text(original, {"material": "帆布", "product_name": "帆布托特"}, field="body")
    assert issues
    assert issues[0]["original"] == original
    assert "未确认" in issues[0]["reason"]
    assert original == "帆布包适合通勤，没有未确认的功能"


def test_grounded_not_waterproof_is_not_keyword_blocked():
    facts = {"material": "帆布", "product_name": "帆布托特", "waterproof": "not_waterproof"}
    assert screen_consumer_copy(title="帆布托特", body="这只包不防水", hashtags=["箱包"], facts=facts)["ok"] is True
    denied = screen_consumer_copy(
        title="帆布托特",
        body="这只包不防水",
        hashtags=[],
        facts={"material": "帆布", "waterproof": "needs_confirmation"},
    )
    assert denied["ok"] is False
    confirmed = screen_consumer_copy(
        title="帆布托特",
        body="这只包防水",
        hashtags=[],
        facts={"waterproof": "confirmed", "material": "帆布"},
    )
    assert confirmed["ok"] is True


def test_qualified_body_does_not_excuse_ungrounded_title():
    facts = {"product_name": "帆布托特", "material": "帆布", "waterproof": "needs_confirmation"}
    result = screen_consumer_copy(
        title="通勤托特",
        body="材质是帆布。",
        hashtags=["箱包"],
        facts=facts,
        cards=[{"title": "上班搭法", "body": "材质：帆布"}],
    )
    assert result["ok"] is False
    fields = {item["field"] for item in result["issues"]}
    assert "title" in fields
    assert "card:0:title" in fields
    assert "body" not in fields
    assert "hashtag" not in fields
    assert result["issues"][0]["original"] == "通勤托特"


def test_material_only_does_not_invent_cards():
    plan = plan_xiaohongshu(
        {"product_name": "帆布托特", "material": "帆布", "waterproof": "needs_confirmation"},
        title="帆布托特",
        body="材质是帆布。",
    )
    assert plan["code"] is None
    content = [card for card in plan["cards"] if card["role"] == "card"]
    assert len(plan["cards"]) == 2
    assert len(content) == 1
    bodies = [card["body"] for card in plan["cards"]]
    assert len(bodies) == len(set(bodies))
    joined = "".join(bodies)
    for invented in ("容量", "承重", "厘米", "体验", "防水"):
        assert invented not in joined
    assert plan["missing"] == []
    assert plan["supplements_required"] is False
    assert plan["need_more_cards"] == 0


def test_one_confirmed_material_can_make_one_ordered_content_card():
    facts = {"product_name": "帆布托特", "material": "帆布", "waterproof": "needs_confirmation"}
    plan = plan_xiaohongshu(facts, title="帆布托特", body="看看这款帆布托特的外观与细节。")
    assert plan["code"] is None
    assert plan["need_more_cards"] == 0
    assert [(card["role"], card["purpose"], card["cites"]) for card in plan["cards"]] == [
        ("cover", "识别商品", ["product_name", "material"]),
        ("card", "材质", ["material"]),
    ]
    assert "防水" not in "".join(card["body"] for card in plan["cards"])


def test_name_and_unknown_waterproof_alone_cannot_make_content_cards():
    plan = plan_xiaohongshu(
        {"product_name": "帆布托特", "waterproof": "needs_confirmation"},
        title="帆布托特", body="看看这款帆布托特。",
    )
    assert plan["code"] == "content_insufficient"
    assert plan["need_more_cards"] == 1
    assert [card["role"] for card in plan["cards"]] == ["cover"]


def test_five_cards_use_distinct_locked_facts():
    facts = {
        "product_name": "帆布托特",
        "material": "帆布",
        "size": "高30厘米",
        "capacity": "可放A4",
        "load": "5公斤",
        "scene": "通勤携带",
        "scene_evidence": "商品页写明通勤携带",
        "waterproof": "not_waterproof",
    }
    plan = plan_xiaohongshu(facts, title="帆布托特", body="材质是帆布，不防水。")
    assert plan["code"] is None
    assert len(plan["cards"]) == 5
    bodies = [card["body"] for card in plan["cards"]]
    purposes = [card["purpose"] for card in plan["cards"]]
    assert len(set(bodies)) == 5
    assert len(set(purposes)) == 5
    assert screen_consumer_copy(
        title=plan["cards"][0]["title"],
        body=plan["cards"][0]["body"],
        hashtags=[],
        facts=facts,
        cards=plan["cards"],
    )["ok"] is True
