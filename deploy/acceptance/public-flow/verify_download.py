"""Check the actual browser-downloaded archive without touching the application."""

from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[3]
state = json.loads((ROOT / "deploy/runtime/public-flow-state.json").read_text(encoding="utf-8"))
archive = ROOT / "deploy/runtime" / f"campaign-{state['campaign_id']}-douyin-v3.zip"
with ZipFile(archive) as package:
    names = package.namelist()
    manifest = json.loads(package.read("manifest.json"))
    assert manifest["campaign_id"] == state["campaign_id"]
    assert manifest["platform"] == "douyin" and manifest["version"] == 3
    assert manifest["reviewed_at"]
    assert [entry["file"] for entry in manifest["files"]] == names[:-1]
    assert [entry["role"] for entry in manifest["files"][:2]] == ["cover", "final_video"]
    for entry in manifest["files"]:
        assert sha256(package.read(entry["file"])).hexdigest() == entry["sha256"]
    for name in ("title.txt", "body.txt", "hashtags.txt"):
        text = package.read(name).decode("utf-8")
        assert not any(word in text for word in ("内部核查", "测试信息", "部署信息", "MockTransport"))
print(f"browser_zip_verified platform=douyin version=3 files={len(manifest['files'])} bytes={archive.stat().st_size} sha256={sha256(archive.read_bytes()).hexdigest()}")
