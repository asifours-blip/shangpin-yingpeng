"""从常见图片头读取宽高。不引入 Pillow；解析失败返回 None。"""

from __future__ import annotations

import struct


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data or len(data) < 10:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png(data)
    if data[:2] == b"\xff\xd8":
        return _jpeg(data)
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return _gif(data)
    if data[:2] == b"BM":
        return _bmp(data)
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp(data)
    return None


def _png(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return _ok(width, height)


def _gif(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    width, height = struct.unpack("<HH", data[6:10])
    return _ok(width, height)


def _bmp(data: bytes) -> tuple[int, int] | None:
    if len(data) < 26:
        return None
    width, height = struct.unpack("<ii", data[18:26])
    return _ok(width, abs(height))


def _jpeg(data: bytes) -> tuple[int, int] | None:
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 3 >= n:
            break
        seglen = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if seglen < 2:
            break
        # SOF0–SOF3 / SOF5–SOF7 / SOF9–SOF11 / SOF13–SOF15
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return _ok(width, height)
        i += 2 + seglen
    return None


def _webp(data: bytes) -> tuple[int, int] | None:
    # RIFF header 12 字节后是 chunk：fourcc + size
    offset = 12
    n = len(data)
    while offset + 8 <= n:
        fourcc = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        payload = offset + 8
        if fourcc == b"VP8X" and payload + 10 <= n:
            w = 1 + int.from_bytes(data[payload + 4 : payload + 7], "little")
            h = 1 + int.from_bytes(data[payload + 7 : payload + 10], "little")
            return _ok(w, h)
        if fourcc == b"VP8 " and payload + 10 <= n:
            # 跳过 3 字节 frame tag，同步码 0x9d012a，随后宽高各 2 字节（低 14 位）
            if data[payload + 3 : payload + 6] == b"\x9d\x01\x2a":
                w = struct.unpack("<H", data[payload + 6 : payload + 8])[0] & 0x3FFF
                h = struct.unpack("<H", data[payload + 8 : payload + 10])[0] & 0x3FFF
                return _ok(w, h)
        if fourcc == b"VP8L" and payload + 5 <= n and data[payload] == 0x2F:
            bits = struct.unpack("<I", data[payload + 1 : payload + 5])[0]
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return _ok(w, h)
        offset = payload + size + (size & 1)
    return None


def _ok(width: int, height: int) -> tuple[int, int] | None:
    if width <= 0 or height <= 0:
        return None
    return width, height
