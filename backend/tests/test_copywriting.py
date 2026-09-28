"""独立营销文案 API 测试。方舟用替身，禁止真网。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import patch

from app.models import CopywritingOperation
from app.services.ark import ArkError
from app.services.copy_templates import build_copy_prompts, prompt_file_for
from app.services.risk_lexicon import check_text
from tests.conftest import as_user, cleanup, make_asset, make_user


def test_creation_direction_is_separate_from_product_facts():
    facts = {"product_name": "帆布托特", "material": "帆布", "waterproof": "needs_confirmation"}
    _system, user = build_copy_prompts(
        "douyin", facts, generation_requirements="用通勤场景叙述，不增加未经确认的功能"
    )
    assert "用通勤场景叙述" in user
    assert "不构成商品事实" in user
    assert facts == {"product_name": "帆布托特", "material": "帆布", "waterproof": "needs_confirmation"}


def test_source_reference_reaches_prompt_without_becoming_product_fact():
    facts = {"product_name": "帆布托特", "material": "帆布"}
    _system, user = build_copy_prompts(
        "xiaohongshu", facts,
        source_references=[{"source_item_id": 7, "title": "通勤场景参考"}],
    )
    assert "通勤场景参考" in user
    assert "不是自家商品事实" in user
    assert facts == {"product_name": "帆布托特", "material": "帆布"}

_VALID = {
    "title": "今日分享一件衣服",
    "body": "上身很舒服，日常也能穿。",
    "hashtags": ["穿搭日常"],
    "facts_to_confirm": ["面料成分待核对"],
}

_EDIT_RISKY = {
    "title": "第一名好物分享",
    "body": "亲测好用，日常也能穿。",
    "hashtags": ["穿搭"],
    "facts_to_confirm": [],
}


@contextmanager
def stub_ark(result: str | BaseException):
    """挡住方舟和 MinIO。模型调用在文案生成入口，不在 HTTP 路由里。"""
    if isinstance(result, BaseException):
        vision = patch("app.services.copywriting_generator.chat_vision", side_effect=result)
    else:
        vision = patch("app.services.copywriting_generator.chat_vision", return_value=result)
    storage = patch(
        "app.api.copywriting.storage.get_bytes",
        return_value=b"\x89PNG\r\n\x1a\nfakeimg",
    )
    with vision, storage:
        yield


def _op_id_from_502(resp) -> int:
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), detail
    assert "operation_id" in detail
    return int(detail["operation_id"])


def test_copy_prompts_load_from_markdown():
    xhs = prompt_file_for("xiaohongshu")
    dy = prompt_file_for("douyin")
    assert xhs.is_file(), xhs
    assert dy.is_file(), dy
    assert "小红书" in xhs.name
    assert "抖音" in dy.name

    xhs_sys, xhs_user = build_copy_prompts(
        "xiaohongshu",
        {"product_name": "菱格包", "selling_points": "轻便", "campaign": "春季上新"},
    )
    assert "生活方式表达" in xhs_sys
    assert "仅返回合法JSON" in xhs_sys
    assert "JSON 传输约束" in xhs_sys
    assert "商品名称：菱格包" in xhs_user
    assert "已确认的商品卖点：轻便" in xhs_user
    assert "其他要求：春季上新" in xhs_user
    assert "{{" not in xhs_user

    dy_sys, dy_user = build_copy_prompts("douyin", {"product_name": "保温杯"})
    assert "短视频口播" in dy_sys
    assert "商品名称：保温杯" in dy_user
    assert "期望口播时长：30—45秒" in dy_user
    assert "{{" not in dy_user


def test_create_accepts_body_with_raw_newlines(client, db):
    """口播/笔记常把换行直接写进 body，strict JSON 会挂。"""
    user = make_user(db)
    asset = make_asset(db, user)
    as_user(user)
    raw = (
        '{\n'
        '  "title": "今日分享一件衣服",\n'
        '  "body": "第一段\n第二段",\n'
        '  "hashtags": ["穿搭日常"],\n'
        '  "facts_to_confirm": []\n'
        '}'
    )
    try:
        with stub_ark(raw):
            resp = client.post(
                "/api/copywriting",
                json={"asset_id": asset.id, "platform": "douyin"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "succeeded"
        assert resp.json()["generated_content"]["body"] == "第一段\n第二段"
    finally:
        cleanup(db, user)


def test_create_succeeded_two_platforms_in_list(client, db):
    user = make_user(db)
    asset = make_asset(db, user)
    as_user(user)
    try:
        with stub_ark(json.dumps(_VALID)):
            r1 = client.post(
                "/api/copywriting",
                json={"asset_id": asset.id, "platform": "douyin"},
            )
            r2 = client.post(
                "/api/copywriting",
                json={"asset_id": asset.id, "platform": "xiaohongshu"},
            )
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r1.json()["status"] == "succeeded"
        assert r2.json()["status"] == "succeeded"
        assert r1.json()["platform"] == "douyin"
        assert r2.json()["platform"] == "xiaohongshu"
        assert r1.json()["generated_content"]["title"] == _VALID["title"]

        db.expire_all()
        row = db.get(CopywritingOperation, r1.json()["id"])
        assert row is not None
        assert row.status == "succeeded"

        listed = client.get("/api/copywriting")
        assert listed.status_code == 200
        items = listed.json()["items"]
        ids = {item["id"] for item in items}
        assert r1.json()["id"] in ids
        assert r2.json()["id"] in ids
        platforms = {item["platform"] for item in items}
        assert platforms == {"douyin", "xiaohongshu"}
    finally:
        cleanup(db, user)


def test_missing_title_returns_502_and_persists_failed(client, db):
    user = make_user(db)
    asset = make_asset(db, user)
    as_user(user)
    payload = json.dumps(
        {
            "body": "只有正文没有标题",
            "hashtags": [],
            "facts_to_confirm": [],
        }
    )
    try:
        with stub_ark(payload):
            r = client.post(
                "/api/copywriting",
                json={"asset_id": asset.id, "platform": "douyin"},
            )
        assert r.status_code == 502, r.text
        op_id = _op_id_from_502(r)

        listed = client.get("/api/copywriting")
        assert listed.status_code == 200
        found = [item for item in listed.json()["items"] if item["id"] == op_id]
        assert found, "失败记录不应被 rollback 掉"
        assert found[0]["status"] == "failed"

        db.expire_all()
        row = db.get(CopywritingOperation, op_id)
        assert row is not None
        assert row.status == "failed"
    finally:
        cleanup(db, user)


def test_ark_error_returns_502_and_persists_failed(client, db):
    user = make_user(db)
    asset = make_asset(db, user)
    as_user(user)
    try:
        with stub_ark(ArkError("方舟调用失败")):
            r = client.post(
                "/api/copywriting",
                json={"asset_id": asset.id, "platform": "douyin"},
            )
        assert r.status_code == 502, r.text
        op_id = _op_id_from_502(r)

        db.expire_all()
        row = db.get(CopywritingOperation, op_id)
        assert row is not None
        assert row.status == "failed"
        assert row.error_message
    finally:
        cleanup(db, user)


def test_patch_updates_risk_processing_forbids_patch_delete(client, db):
    user = make_user(db)
    asset = make_asset(db, user)
    as_user(user)
    try:
        with stub_ark(json.dumps(_VALID)):
            created = client.post(
                "/api/copywriting",
                json={"asset_id": asset.id, "platform": "douyin"},
            )
        assert created.status_code == 200, created.text
        op_id = created.json()["id"]
        assert created.json()["status"] == "succeeded"
        before = created.json()["risk_result"]
        assert before is not None
        assert before["need_human_review"] is False

        patched = client.patch(
            f"/api/copywriting/{op_id}",
            json={"edited_content": _EDIT_RISKY},
        )
        assert patched.status_code == 200, patched.text
        risk = patched.json()["risk_result"]
        assert risk["need_human_review"] is True
        assert any(hit["fragment"] == "第一" for hit in risk["hits"])
        assert patched.json()["edited_content"]["title"] == _EDIT_RISKY["title"]

        processing = CopywritingOperation(
            user_id=user.id,
            input_asset_id=asset.id,
            platform="douyin",
            product_facts={},
            status="processing",
        )
        db.add(processing)
        db.commit()
        db.refresh(processing)

        blocked_patch = client.patch(
            f"/api/copywriting/{processing.id}",
            json={"edited_content": _VALID},
        )
        assert blocked_patch.status_code == 400
        blocked_del = client.delete(f"/api/copywriting/{processing.id}")
        assert blocked_del.status_code == 400
    finally:
        cleanup(db, user)


def test_soft_delete_hidden_from_owner_visible_to_admin(client, db):
    user = make_user(db)
    admin = make_user(db, role="admin")
    asset = make_asset(db, user)
    as_user(user)
    try:
        with stub_ark(json.dumps(_VALID)):
            created = client.post(
                "/api/copywriting",
                json={"asset_id": asset.id, "platform": "douyin"},
            )
        assert created.status_code == 200, created.text
        op_id = created.json()["id"]

        deleted = client.delete(f"/api/copywriting/{op_id}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["user_deleted_at"] is not None

        listed = client.get("/api/copywriting")
        assert listed.status_code == 200
        assert all(item["id"] != op_id for item in listed.json()["items"])

        as_user(admin)
        admin_list = client.get("/api/admin/copywriting")
        assert admin_list.status_code == 200
        hit = next(
            (item for item in admin_list.json()["items"] if item["id"] == op_id),
            None,
        )
        assert hit is not None
        assert hit["user_deleted_at"] is not None
    finally:
        cleanup(db, user, admin)


def test_others_asset_404(client, db):
    owner = make_user(db)
    other = make_user(db)
    foreign = make_asset(db, other)
    as_user(owner)
    try:
        r = client.post(
            "/api/copywriting",
            json={"asset_id": foreign.id, "platform": "douyin"},
        )
        assert r.status_code == 404
    finally:
        cleanup(db, owner, other)


def test_risk_lexicon_single_char_zui_is_not_a_hit():
    empty = check_text("最")
    assert empty.hits == []
    first = check_text("第一")
    assert any(hit.fragment == "第一" for hit in first.hits)
