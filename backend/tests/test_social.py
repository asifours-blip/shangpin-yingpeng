"""演示社交账号 / 联系人：样例接入，不接真实 OAuth。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import as_user, cleanup, make_user

SAMPLE_DY_01 = {"platform": "douyin", "external_account_id": "sample_dy_01"}
SAMPLE_DY_02 = {"platform": "douyin", "external_account_id": "sample_dy_02"}
SAMPLE_XHS_01 = {"platform": "xiaohongshu", "external_account_id": "sample_xhs_01"}


def _connect(client: TestClient, body: dict):
    return client.post("/api/social/accounts", json=body)


def _account_ids(items: list[dict]) -> set[int]:
    return {row["id"] for row in items}


def _sources(items: list[dict]) -> set[tuple[str, str]]:
    return {(row["platform"], row["external_account_id"]) for row in items}


def test_connect_sample_auto_contacts_and_reject_invalid(client: TestClient, db) -> None:
    user = make_user(db)
    as_user(user)
    try:
        r = _connect(client, SAMPLE_DY_01)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["platform"] == "douyin"
        assert body["external_account_id"] == "sample_dy_01"
        assert body["status"] == "connected"
        account_id = body["id"]

        contacts = client.get(f"/api/social/accounts/{account_id}/contacts")
        assert contacts.status_code == 200, contacts.text
        assert len(contacts.json()["items"]) > 0

        bad_pairs = [
            {"platform": "douyin", "external_account_id": "sample_xhs_01"},
            {"platform": "xiaohongshu", "external_account_id": "sample_dy_01"},
            {"platform": "douyin", "external_account_id": "sample_not_exist"},
            {"platform": "weibo", "external_account_id": "sample_dy_01"},
        ]
        for pair in bad_pairs:
            bad = _connect(client, pair)
            assert bad.status_code == 400, (pair, bad.text)
    finally:
        cleanup(db, user)


def test_reconnect_same_combo_does_not_insert_second_row(client: TestClient, db) -> None:
    user = make_user(db)
    as_user(user)
    try:
        first = _connect(client, SAMPLE_DY_01)
        assert first.status_code == 200, first.text
        account_id = first.json()["id"]

        second = _connect(client, SAMPLE_DY_01)
        assert second.status_code == 200, second.text
        assert second.json()["id"] == account_id
        assert second.json()["status"] == "connected"

        listed = client.get("/api/social/accounts")
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == account_id
        assert items[0]["status"] == "connected"
    finally:
        cleanup(db, user)


def test_aggregate_two_sources_remark_survives_sync(client: TestClient, db) -> None:
    user = make_user(db)
    as_user(user)
    try:
        dy = _connect(client, SAMPLE_DY_01)
        xhs = _connect(client, SAMPLE_XHS_01)
        assert dy.status_code == 200, dy.text
        assert xhs.status_code == 200, xhs.text
        dy_id = dy.json()["id"]

        agg = client.get("/api/social/contacts")
        assert agg.status_code == 200, agg.text
        items = agg.json()["items"]
        assert _sources(items) >= {
            ("douyin", "sample_dy_01"),
            ("xiaohongshu", "sample_xhs_01"),
        }

        target = next(row for row in items if row["account_id"] == dy_id)
        patched = client.patch(
            f"/api/social/contacts/{target['id']}",
            json={"remark": "演示备注-wave4", "tags": ["演示标签"]},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["remark"] == "演示备注-wave4"
        assert patched.json()["tags"] == ["演示标签"]

        synced = client.post(f"/api/social/accounts/{dy_id}/sync")
        assert synced.status_code == 200, synced.text
        assert synced.json()["synced"] > 0

        after = client.get(f"/api/social/accounts/{dy_id}/contacts")
        assert after.status_code == 200, after.text
        kept = next(row for row in after.json()["items"] if row["id"] == target["id"])
        assert kept["remark"] == "演示备注-wave4"
        assert kept["tags"] == ["演示标签"]
    finally:
        cleanup(db, user)


def test_disconnect_hides_from_aggregate_reconnect_restores(client: TestClient, db) -> None:
    user = make_user(db)
    as_user(user)
    try:
        dy = _connect(client, SAMPLE_DY_01)
        xhs = _connect(client, SAMPLE_XHS_01)
        assert dy.status_code == 200, dy.text
        assert xhs.status_code == 200, xhs.text
        dy_id = dy.json()["id"]
        xhs_id = xhs.json()["id"]

        disconnected = client.patch(
            f"/api/social/accounts/{dy_id}",
            json={"status": "disconnected"},
        )
        assert disconnected.status_code == 200, disconnected.text
        assert disconnected.json()["status"] == "disconnected"

        after_off = client.get("/api/social/contacts")
        assert after_off.status_code == 200, after_off.text
        remaining = after_off.json()["items"]
        assert all(row["account_id"] != dy_id for row in remaining)
        assert any(row["account_id"] == xhs_id for row in remaining)

        reconnected = _connect(client, SAMPLE_DY_01)
        assert reconnected.status_code == 200, reconnected.text
        assert reconnected.json()["id"] == dy_id
        assert reconnected.json()["status"] == "connected"

        after_on = client.get("/api/social/contacts")
        assert after_on.status_code == 200, after_on.text
        restored = after_on.json()["items"]
        assert any(row["account_id"] == dy_id for row in restored)
        assert any(row["account_id"] == xhs_id for row in restored)
    finally:
        cleanup(db, user)


def test_other_user_gets_404_for_same_ids(client: TestClient, db) -> None:
    owner = make_user(db)
    other = make_user(db)
    try:
        as_user(owner)
        created = _connect(client, SAMPLE_DY_01)
        assert created.status_code == 200, created.text
        account_id = created.json()["id"]

        contacts = client.get(f"/api/social/accounts/{account_id}/contacts")
        assert contacts.status_code == 200, contacts.text
        contact_id = contacts.json()["items"][0]["id"]

        as_user(other)
        assert client.get(f"/api/social/accounts/{account_id}/contacts").status_code == 404
        assert (
            client.patch(
                f"/api/social/accounts/{account_id}",
                json={"status": "disconnected"},
            ).status_code
            == 404
        )
        assert client.post(f"/api/social/accounts/{account_id}/sync").status_code == 404
        assert (
            client.patch(
                f"/api/social/contacts/{contact_id}",
                json={"remark": "不应写入"},
            ).status_code
            == 404
        )
    finally:
        cleanup(db, owner, other)


def test_all_sample_accounts_can_connect_same_user(client: TestClient, db) -> None:
    user = make_user(db)
    as_user(user)
    try:
        ids = []
        for body in (SAMPLE_DY_01, SAMPLE_DY_02, SAMPLE_XHS_01):
            r = _connect(client, body)
            assert r.status_code == 200, (body, r.text)
            assert r.json()["status"] == "connected"
            assert r.json()["external_account_id"] == body["external_account_id"]
            ids.append(r.json()["id"])

        listed = client.get("/api/social/accounts")
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert _account_ids(items) == set(ids)
        assert _sources(items) == {
            ("douyin", "sample_dy_01"),
            ("douyin", "sample_dy_02"),
            ("xiaohongshu", "sample_xhs_01"),
        }
    finally:
        cleanup(db, user)
