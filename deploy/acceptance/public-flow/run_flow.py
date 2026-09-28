"""API-side acceptance of the public copy's real campaign and delivery services.

Run prepare, then one bounded Compose tick, then edit, another tick, and finish.
Only the chat HTTP upstream is synthetic; database, MinIO and FFmpeg are real.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import io
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / "deploy" / "runtime" / "public-flow-state.json"
BASE = "http://127.0.0.1:18260"
JAR = http.cookiejar.CookieJar()
HTTP = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(JAR))


def request(method: str, path: str, *, payload=None, headers=None, raw=False):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    supplied = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        supplied["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=supplied, method=method)
    try:
        with HTTP.open(req, timeout=120) as response:
            body = response.read()
            return (response.status, body) if raw else json.loads(body or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} returned HTTP {exc.code}: {detail}") from None


def login():
    # This fixed demo login is created by the public application's empty-DB seed.
    request("POST", "/api/auth/login", payload={"username": "demo", "password": "demo123"})
    assert request("GET", "/api/auth/me")["username"] == "demo"


def upload_image() -> int:
    image = (ROOT / "backend/tests/fixtures/canvas-tote.jpg").read_bytes()
    boundary = "public-flow-fixed-boundary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"synthetic-tote.jpg\"\r\n"
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + image + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/api/assets", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with HTTP.open(req, timeout=60) as response:
        assert response.status == 200
        return json.load(response)["id"]


def current_review(campaign_id: int):
    return request("GET", f"/api/campaigns/{campaign_id}/review")


def prepare():
    login()
    asset_id = upload_image()
    suffix = int(time.time())
    product = request("POST", "/api/products", payload={
        "sku": f"PUBLIC-SYNTHETIC-{suffix}",
        "name": "合成示例·帆布手提袋",
        "primary_asset_id": asset_id,
        "facts": {"product_name": "帆布手提袋", "material": "帆布", "waterproof": "needs_confirmation"},
        "claim_evidence": {"material": "人工定义的隔离合成商品事实"},
    })
    fact_id = product["latest_fact"]["id"]
    campaign = request("POST", "/api/campaigns", payload={
        "product_id": product["id"], "fact_version_id": fact_id,
        "source_item_ids": [], "target_platforms": ["douyin", "xiaohongshu"],
        "generation_requirements": "展示商品外观和已确认的帆布材质，不宣称防水或未知规格",
        "generation_budget": 12,
    })
    path = f"/api/campaigns/{campaign['id']}/start"
    key = f"public-flow-{suffix}"
    first = request("POST", path, headers={"Idempotency-Key": key})
    second = request("POST", path, headers={"Idempotency-Key": key})
    assert first["run"]["id"] == second["run"]["id"]
    assert set(first["target_platforms"]) == {"douyin", "xiaohongshu"}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "asset_id": asset_id, "product_id": product["id"], "fact_id": fact_id,
        "campaign_id": campaign["id"], "run_id": first["run"]["id"],
    }, indent=2), encoding="utf-8")
    print(f"prepare_ok product={product['id']} campaign={campaign['id']} run={first['run']['id']} idempotent=true")


def edit():
    login()
    state = json.loads(STATE.read_text(encoding="utf-8"))
    campaign_id = state["campaign_id"]
    review = current_review(campaign_id)
    by_platform = {item["platform"]: item for item in review["platforms"]}
    for platform in ("douyin", "xiaohongshu"):
        current = by_platform[platform]["current"]
        assert current["version"] == 1, (platform, current["version"])
        assert not by_platform[platform]["blockers"], (platform, by_platform[platform]["blockers"])
        assert current["assets"], platform
    old_title = by_platform["douyin"]["current"]["title"]
    changed = request("PATCH", f"/api/campaigns/{campaign_id}/variants/douyin", payload={
        "expected_version": 1, "title": f"{old_title} · 日常搭配",
    })
    assert changed["version"] == 2
    after = current_review(campaign_id)
    douyin = next(item for item in after["platforms"] if item["platform"] == "douyin")
    assert douyin["current"]["version"] == 2
    assert douyin["current"]["status"] != "approved"
    print(f"edit_ok campaign={campaign_id} douyin_version=2 media_regeneration_queued=true")


def check_zip(campaign_id: int, platform: str, version: int):
    path = f"/api/campaigns/{campaign_id}/variants/{platform}/export?version={version}"
    status, _ = request("HEAD", path, raw=True)
    assert status == 200
    status, archive = request("GET", path, raw=True)
    assert status == 200
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = bundle.namelist()
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["campaign_id"] == campaign_id
        assert manifest["platform"] == platform
        assert manifest["version"] == version
        assert manifest["reviewed_at"]
        assert [entry["file"] for entry in manifest["files"]] == names[:-1]
        for entry in manifest["files"]:
            assert hashlib.sha256(bundle.read(entry["file"])).hexdigest() == entry["sha256"]
        if platform == "douyin":
            assert [entry["role"] for entry in manifest["files"][:2]] == ["cover", "final_video"]
        else:
            assert [entry["role"] for entry in manifest["files"][:2]] == ["cover", "card"]
            assert [entry["position"] for entry in manifest["files"][:2]] == [0, 1]
        for name in ("title.txt", "body.txt", "hashtags.txt"):
            copy = bundle.read(name).decode("utf-8")
            assert not any(term in copy for term in ("内部核查", "测试信息", "部署信息", "MockTransport"))
    return len(archive), hashlib.sha256(archive).hexdigest(), len(manifest["files"])


def finish():
    login()
    state = json.loads(STATE.read_text(encoding="utf-8"))
    campaign_id = state["campaign_id"]
    review = current_review(campaign_id)
    for item in review["platforms"]:
        assert not item["blockers"], (item["platform"], item["blockers"])
        version = item["current"]["version"]
        approved = request("POST", f"/api/campaigns/{campaign_id}/variants/{item['platform']}/approve", payload={
            "expected_version": version,
        })
        assert approved["decision"] == "approved"
    after = current_review(campaign_id)
    assert after["status"] == "approved", after["status"]
    for item in after["platforms"]:
        platform = item["platform"]
        version = item["current"]["version"]
        size, digest, files = check_zip(campaign_id, platform, version)
        print(f"delivery_ok campaign={campaign_id} platform={platform} version={version} files={files} bytes={size} sha256={digest}")
    desk = request("GET", f"/api/campaigns/{campaign_id}/publish")
    assert desk["platforms"]
    print(f"finish_ok campaign={campaign_id} status=approved publish_desk_read=true live_publish=0")


if __name__ == "__main__":
    actions = {"prepare": prepare, "edit": edit, "finish": finish}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("usage: run_flow.py prepare|edit|finish")
    actions[sys.argv[1]]()
