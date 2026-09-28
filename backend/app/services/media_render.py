"""本地排版成片和组图。消费者画面只用可发布的文案和已确认事实。

未确认项、放不下的文字、以及「不是方舟原片」都写进质检结果，不画在画面上。
音轨是项目自制的四音短句，有起音和留白，不是持续提示音，也不是第三方录音。
"""

from __future__ import annotations

import json
import math
import struct
import subprocess
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.services.campaign_pipeline import text_asserts_unconfirmed
from app.services.consumer_qc import INTERNAL_PHRASES

FONT_PATH = Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
MIN_SECONDS = 15
MAX_SECONDS = 30
MIN_FRAME_SECONDS = 1.5
MIN_PHOTO_RATIO = 0.46
AUDIO_SOURCE = {
    "id": "project_synth_phrase_v2",
    "license": "owned_original_synthesis",
    "description": "项目用 Python wave 自制的四音短句（C4、E4、G4、E4），每 4 秒一轮，拍间留白。无第三方曲库。",
}


class RenderError(Exception):
    pass


def publishable_lines(facts: dict) -> list[str]:
    """只放已经能对消费者说的事实。未确认能力不进画面。"""
    lines: list[str] = []
    material = facts.get("material")
    if material:
        lines.append(f"材质：{material}")
    name = facts.get("product_name")
    if name:
        lines.append(str(name))
    if facts.get("waterproof") == "confirmed":
        lines.append("防水")
    return lines


def review_notes(facts: dict) -> list[str]:
    notes = ["本地排版并配自制短句音轨，不是方舟原生成片，不能代替联调验收"]
    if facts.get("waterproof") not in (None, "", "confirmed"):
        notes.append("防水未确认，消费者画面不写防水卖点")
    return notes


def fact_lines(facts: dict) -> list[str]:
    return publishable_lines(facts)


def probe_media(path: Path) -> dict:
    """正常安装的 ffprobe，用 JSON 读编码、宽高、时长和音轨。"""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RenderError("未安装 ffprobe，不能用 ffmpeg 文本输出冒充探测") from exc
    if proc.returncode != 0:
        raise RenderError((proc.stderr or "ffprobe 失败")[-400:])
    try:
        data = json.loads(proc.stdout)
        video = next(item for item in data.get("streams", []) if item.get("codec_type") == "video")
        audio = next((item for item in data.get("streams", []) if item.get("codec_type") == "audio"), None)
        duration = float(data["format"]["duration"])
    except (json.JSONDecodeError, StopIteration, KeyError, TypeError, ValueError) as exc:
        raise RenderError(f"ffprobe JSON 无法解析: {(proc.stdout or '')[:300]}") from exc
    return {
        "tool": "ffprobe",
        "codec": video.get("codec_name"),
        "audio_codec": None if audio is None else audio.get("codec_name"),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "duration": duration,
    }


def layout_pages(
    title: str,
    body: str,
    extras: list[str],
    *,
    width: int,
    height: int,
    max_pages: int,
    split_extras: bool = False,
) -> dict:
    """按像素容量分页。放不下时带回 qc_issue 和未放下的原文，不截掉不说。"""
    draw = ImageDraw.Draw(Image.new("RGB", (width, height)))
    title_font = _font(max(36, width // 16))
    body_font = _font(max(28, width // 26))
    title_lh = int(title_font.size * 1.35)
    body_lh = int(body_font.size * 1.35)
    avail = _text_budget(height)
    if avail < title_lh:
        raise RenderError("画面高度不够排字")
    title_lines = _wrap(draw, title or "商品", title_font, width - 96)
    body_lines = _wrap(draw, body, body_font, width - 96) if body else []
    extra_lines = []
    for item in extras:
        extra_lines.extend(_wrap(draw, item, body_font, width - 96))

    title_budget = max(1, avail // title_lh)
    if len(title_lines) > title_budget:
        return {
            "pages": [{"title": title_lines[0], "lines": title_lines[1:title_budget]}],
            "qc_issue": "title_overflow",
            "unplaced": "".join(title_lines[title_budget:]) + body + "".join(extra_lines),
        }

    pages: list[dict] = []
    title_block = title_lh * len(title_lines)
    first_room = avail - title_block
    first_slots = max(0, first_room // body_lh)
    # 后续页仍会画一行标题，槽位不能把这块高度再分给正文。
    later_slots = max(0, (avail - title_lh) // body_lh)
    stream = body_lines if split_extras else body_lines + extra_lines
    extra_pages = extra_lines if split_extras else []
    first_lines = stream[:first_slots]
    rest = stream[first_slots:]
    pages.append({"title": "\n".join(title_lines), "lines": first_lines})
    while rest and later_slots > 0 and len(pages) < max_pages:
        pages.append({"title": title_lines[0], "lines": rest[:later_slots]})
        rest = rest[later_slots:]
    parked: list[str] = list(rest)
    for line in extra_pages:
        if len(pages) >= max_pages:
            parked.append(line)
        else:
            pages.append({"title": title_lines[0], "lines": [line]})
    unplaced = "".join(parked)
    issue = "body_overflow" if unplaced else None
    return {"pages": pages[:max_pages], "qc_issue": issue, "unplaced": unplaced}


def _guard_consumer_text(*, title: str, body: str = "", lines: list[str] | None = None, hashtags: list[str] | None = None) -> None:
    chunks = [title, body, *(lines or []), *(hashtags or [])]
    for chunk in chunks:
        for phrase in INTERNAL_PHRASES:
            if phrase and phrase in (chunk or ""):
                raise RenderError(f"内部措辞「{phrase}」不能进入排版，原文已保留在质检结果")


def render_cover(source: Path, dest: Path, *, width: int, height: int, title: str, lines: list[str] | None = None) -> None:
    _guard_consumer_text(title=title, lines=lines)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _paint(source, dest, width=width, height=height, title=title, lines=lines or [])


def render_story_clip(
    source: Path,
    dest: Path,
    *,
    title: str,
    body: str,
    lines: list[str],
    hashtags: list[str] | None = None,
    review: list[str] | None = None,
) -> dict:
    """15 到 30 秒，带自制短句音轨。核查说明只出现在返回的 review_notes。"""
    _guard_consumer_text(title=title, body=body, lines=lines, hashtags=hashtags)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tags = " ".join(f"#{item}" for item in (hashtags or []) if item)
    extras = [item for item in lines if item]
    if tags:
        extras.append(tags)
    plan = layout_pages(title, body, extras, width=720, height=1280, max_pages=12, split_extras=True)
    pages = plan["pages"] or [{"title": title or "商品", "lines": []}]
    each = _frame_seconds(len(pages))
    if plan["qc_issue"] is None and len(pages) * MIN_FRAME_SECONDS > MAX_SECONDS:
        plan["qc_issue"] = "duration_overflow"
    frame_paths: list[Path] = []
    for index, page in enumerate(pages):
        path = dest.parent / f"frame-{index}.png"
        _paint(source, path, width=720, height=1280, title=page["title"], lines=page["lines"])
        frame_paths.append(path)
    silent = dest.parent / "silent.mp4"
    _encode(frame_paths, silent, each_seconds=each)
    _mux_owned_audio(silent, dest)
    info = probe_media(dest)
    if not (MIN_SECONDS <= info["duration"] <= MAX_SECONDS + 0.4):
        plan["qc_issue"] = plan["qc_issue"] or "duration_out_of_range"
    info["native_model_video"] = False
    info["audio_owned"] = True
    info["audio_source"] = AUDIO_SOURCE["id"]
    info["audio_license"] = AUDIO_SOURCE["license"]
    info["audio_description"] = AUDIO_SOURCE["description"]
    info["qc"] = "needs_review"
    info["qc_issue"] = plan["qc_issue"]
    info["unplaced"] = plan["unplaced"]
    info["review_notes"] = list(review or [])
    info["qc_reason"] = plan["qc_issue"] or "本地排版，不是方舟原生成片，不能代替联调验收"
    info["lines"] = [line for page in pages for line in page["lines"]]
    return info


def render_card_set(
    source: Path,
    dest_dir: Path,
    *,
    title: str,
    body: str = "",
    lines: list[str] | None = None,
    review: list[str] | None = None,
) -> dict:
    """封面加内容卡。放不下的正文记入 qc_issue，不悄悄丢掉。"""
    _guard_consumer_text(title=title, body=body, lines=lines)
    dest_dir.mkdir(parents=True, exist_ok=True)
    plan = layout_pages(title, body, list(lines or []), width=1080, height=1440, max_pages=5, split_extras=True)
    pages = plan["pages"] or [{"title": title or "笔记", "lines": []}]
    paths: list[Path] = []
    for index, page in enumerate(pages):
        path = dest_dir / (f"{index:02d}-cover.png" if index == 0 else f"{index:02d}-card.png")
        _paint(source, path, width=1080, height=1440, title=page["title"], lines=page["lines"])
        paths.append(path)
    return {
        "paths": paths,
        "qc_issue": plan["qc_issue"],
        "unplaced": plan["unplaced"],
        "review_notes": list(review or []),
        "lines": [line for page in pages for line in page["lines"]],
    }


def render_planned_cards(source: Path, dest_dir: Path, cards: list[dict], *, review: list[str] | None = None) -> dict:
    """按结构化卡片出图。每张只画自己的标题和正文。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, card in enumerate(cards):
        card_title = str(card.get("title") or "")
        card_body = str(card.get("body") or "")
        _guard_consumer_text(title=card_title, body=card_body)
        name = f"{index:02d}-cover.png" if card.get("role") == "cover" else f"{index:02d}-card.png"
        path = dest_dir / name
        _paint(
            source,
            path,
            width=1080,
            height=1440,
            title=card_title or "笔记",
            lines=[card_body] if card_body else [],
        )
        paths.append(path)
    return {
        "paths": paths,
        "cards": cards,
        "qc_issue": None,
        "unplaced": "",
        "review_notes": list(review or []),
        "lines": [str(card.get("body") or "") for card in cards if card.get("body")],
    }


def assert_copy_matches_facts(title: str, body: str, facts: dict) -> str | None:
    return text_asserts_unconfirmed(f"{title}\n{body}", facts)


def _frame_seconds(count: int) -> float:
    count = max(1, count)
    # 25fps 取整会吃掉零点几秒，目标落在 18～28 秒，成片仍在 15～30 秒内。
    each = max(18.0 / count, MIN_FRAME_SECONDS)
    if each * count > 28.0:
        each = 28.0 / count
    return each


def _text_budget(height: int) -> int:
    """文字区最多用到商品主体还完整的位置，短文案不必占满。"""
    return max(80, height - int(height * MIN_PHOTO_RATIO) - 36)


def _trim_margins(image: Image.Image) -> Image.Image:
    """去掉近白边，商品主体仍完整留在画面里。"""
    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()

    def background(x: int, y: int) -> bool:
        red, green, blue = pixels[x, y]
        return red > 232 and green > 232 and blue > 232

    min_x, min_y, max_x, max_y = width, height, 0, 0
    found = False
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            if background(x, y):
                continue
            found = True
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
    if not found:
        return rgb
    pad = max(12, int(min(width, height) * 0.04))
    return rgb.crop(
        (
            max(0, min_x - pad),
            max(0, min_y - pad),
            min(width, max_x + pad + 1),
            min(height, max_y + pad + 1),
        )
    )


def _paint(source: Path, dest: Path, *, width: int, height: int, title: str, lines: list[str]) -> None:
    base = _trim_margins(Image.open(source))
    draw_probe = ImageDraw.Draw(Image.new("RGB", (width, height)))
    title_font = _font(max(36, width // 16))
    body_font = _font(max(28, width // 26))
    title_lines = _wrap(draw_probe, title, title_font, width - 96)
    body_lines: list[str] = []
    for raw in lines:
        body_lines.extend(_wrap(draw_probe, raw, body_font, width - 96))
    title_step = int(title_font.size * 1.35)
    body_step = int(body_font.size * 1.35)
    text_px = len(title_lines) * title_step + len(body_lines) * body_step
    gap = 16
    bottom_pad = 12
    text_h = gap + text_px + bottom_pad
    photo_box_h = max(int(height * MIN_PHOTO_RATIO), height - text_h)
    fitted = ImageOps.contain(base, (width, photo_box_h), Image.Resampling.LANCZOS)
    stack = fitted.height + gap + text_px
    top = max(0, (height - bottom_pad - stack) // 2)
    if top + stack + bottom_pad > height:
        top = max(0, height - bottom_pad - stack)
    canvas = Image.new("RGB", (width, height), (248, 243, 236))
    canvas.paste(fitted, ((width - fitted.width) // 2, top))
    draw = ImageDraw.Draw(canvas)
    ink = (42, 32, 28)
    y = top + fitted.height + gap
    limit = height - 4
    leftover: list[str] = []
    for line in title_lines:
        if y + title_step > limit:
            leftover.append(line)
            continue
        draw.text((48, y), line, font=title_font, fill=ink)
        y += title_step
    for line in body_lines:
        if y + body_step > limit:
            leftover.append(line)
            continue
        draw.text((48, y), line, font=body_font, fill=ink)
        y += body_step
    if leftover:
        raise RenderError("版面放不下，拒绝静默截断: " + "".join(leftover)[:80])
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, format="PNG")


def _encode(frames: list[Path], dest: Path, *, each_seconds: float) -> None:
    script = dest.with_name("frames.txt")
    rows: list[str] = []
    for frame in frames:
        rows.append(f"file '{frame.as_posix()}'")
        rows.append(f"duration {each_seconds:.3f}")
    rows.append(f"file '{frames[-1].as_posix()}'")
    script.write_text("\n".join(rows) + "\n", encoding="utf-8")
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(script),
            "-vf",
            "scale=720:1280,format=yuv420p,fps=25",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )


def _write_phrase_wav(path: Path, *, seconds: float = 30, rate: int = 44100) -> None:
    """C4-E4-G4-E4。每 4 秒一轮，音与音之间约 0.3 秒留白。"""
    pattern = ((262.0, 0.0, 0.7), (330.0, 1.0, 0.7), (392.0, 2.0, 0.7), (330.0, 3.0, 0.55))
    frames = bytearray()
    total = int(seconds * rate)
    for index in range(total):
        t = index / rate
        local = t % 4.0
        amp = 0.0
        for freq, start, dur in pattern:
            pos = local - start
            if pos < 0 or pos >= dur:
                continue
            env = 1.0
            if pos < 0.04:
                env = pos / 0.04
            elif pos > dur - 0.08:
                env = max(0.0, (dur - pos) / 0.08)
            amp += 0.22 * env * math.sin(2 * math.pi * freq * t)
        sample = max(-1.0, min(1.0, amp))
        frames += struct.pack("<h", int(sample * 28000))
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)


def _mux_owned_audio(silent: Path, dest: Path) -> None:
    """自制四音短句。wav 里有留白，再压成 AAC。不是持续双正弦。"""
    wav = silent.with_name("owned-phrase.wav")
    bed = silent.with_name("owned-bed.m4a")
    _write_phrase_wav(wav)
    _run(["ffmpeg", "-y", "-i", str(wav), "-c:a", "aac", str(bed)])
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(silent),
            "-i",
            str(bed),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(dest),
        ]
    )


def _font(size: int) -> ImageFont.FreeTypeFont:
    if not FONT_PATH.exists():
        raise RenderError(f"缺少中文字体 {FONT_PATH}")
    return ImageFont.truetype(str(FONT_PATH), size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        if paragraph == "":
            continue
        buf = ""
        for char in paragraph:
            trial = buf + char
            if draw.textlength(trial, font=font) > width and buf:
                lines.append(buf)
                buf = char
            else:
                buf = trial
        if buf:
            lines.append(buf)
    return lines or [""]


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RenderError((proc.stderr or proc.stdout or "ffmpeg 失败")[-500:])
