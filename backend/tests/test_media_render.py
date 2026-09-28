"""ffprobe JSON 探测，画面只用可发布文案，放不下必须报质检。"""

import json
import subprocess
from pathlib import Path

from app.services.media_render import (
    fact_lines,
    layout_pages,
    probe_media,
    render_card_set,
    render_story_clip,
    review_notes,
)


def _still(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x223344:s=80x80",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_ffprobe_json_path(tmp_path: Path):
    source = tmp_path / "bag.png"
    _still(source)
    facts = {"material": "帆布", "waterproof": "needs_confirmation", "product_name": "帆布托特"}
    body = "材质是帆布。"
    info = render_story_clip(
        source,
        tmp_path / "final.mp4",
        title="通勤托特",
        body=body,
        lines=fact_lines(facts),
        hashtags=["箱包"],
        review=review_notes(facts),
    )
    assert info["tool"] == "ffprobe"
    assert info["codec"] == "h264"
    assert info["audio_codec"] == "aac"
    assert info["audio_owned"] is True
    assert info["audio_source"] == "project_synth_phrase_v2"
    assert info["audio_license"] == "owned_original_synthesis"
    assert info["width"] == 720
    assert info["height"] == 1280
    assert 15 <= info["duration"] <= 30.4
    assert info["native_model_video"] is False
    assert info["qc"] == "needs_review"
    assert info["qc_issue"] is None
    painted = "".join(info["lines"])
    assert body in painted
    assert "材质：帆布" in painted
    assert "方舟" not in painted
    assert "不能写成卖点" not in painted
    assert any("防水" in note for note in info["review_notes"])
    assert any("方舟" in note for note in info["review_notes"])
    raw = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(tmp_path / "final.mp4")],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(raw.stdout)
    codecs = {item["codec_name"] for item in payload["streams"]}
    assert "h264" in codecs
    assert "aac" in codecs
    again = probe_media(tmp_path / "final.mp4")
    assert again["tool"] == "ffprobe"
    assert again["audio_codec"] == "aac"
    assert again["duration"] == info["duration"] or abs(again["duration"] - info["duration"]) < 0.05


def test_title_wraps_and_overflow_is_explicit(tmp_path: Path):
    wrapped = layout_pages("通勤帆布托特适合每天上班携带出门", "短正文", [], width=720, height=1280, max_pages=4)
    assert wrapped["qc_issue"] is None
    assert "\n" in wrapped["pages"][0]["title"]
    source = tmp_path / "bag.png"
    _still(source)
    title = "题" * 200
    body = "正文不会被悄悄丢掉"
    result = render_card_set(source, tmp_path / "cards", title=title, body=body, lines=["补充事实"])
    assert result["qc_issue"] == "title_overflow"
    assert body in result["unplaced"]
    assert "题" in result["unplaced"]
    assert result["paths"]
    assert all(path.stat().st_size > 1000 for path in result["paths"])


def test_body_paginates_without_silent_cut(tmp_path: Path):
    source = tmp_path / "bag.png"
    _still(source)
    body = "段" * 4000
    result = render_card_set(source, tmp_path / "cards", title="通勤托特", body=body, lines=["材质：帆布"])
    assert result["qc_issue"] == "body_overflow"
    assert result["unplaced"]
    assert ("".join(result["lines"]) + result["unplaced"]).startswith(body)
    assert len(result["paths"]) <= 5


def test_cards_paint_publishable_text_only(tmp_path: Path):
    source = tmp_path / "bag.png"
    _still(source)
    left = render_card_set(
        source,
        tmp_path / "a",
        title="通勤托特",
        lines=["材质：帆布", "帆布托特", "帆布包适合通勤", "#箱包"],
        review=["防水未确认，消费者画面不写防水卖点"],
    )
    right = render_card_set(
        source,
        tmp_path / "b",
        title="另一只包",
        lines=["材质：皮革", "皮包", "另一段文案", "#皮具"],
        review=["另一条内部说明"],
    )
    assert [path.name for path in left["paths"]] == [
        "00-cover.png",
        "01-card.png",
        "02-card.png",
        "03-card.png",
        "04-card.png",
    ]
    assert "防水" not in "".join(left["lines"])
    assert "未确认" in left["review_notes"][0]
    assert left["paths"][0].read_bytes() != right["paths"][0].read_bytes()
    assert left["paths"][1].read_bytes() != right["paths"][1].read_bytes()
    assert all(path.stat().st_size > 1000 for path in left["paths"])
