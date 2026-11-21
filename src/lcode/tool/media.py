"""Media type sniffing without third-party dependencies.

read_file 只该读文本。以前它把 PNG/MP3 当 UTF-8 硬解，`errors="replace"` 会把
二进制变成几万个替换字符，再被截断成 2000 行垃圾 token——既烧钱又无用。
这里用魔数（magic number）先判定类型，让 read_file 能说人话地拒绝。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

# 判定类型只需要文件头；8192 字节足够覆盖 JPEG 的 SOF 段和 zip 的中央目录。
HEAD_BYTES = 8192

# 文本类 kind：其余 kind 都表示「read_file 不该按文本读」。
KIND_TEXT = "text"

_OFFICE_SUFFIXES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
}

_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


@dataclass(frozen=True)
class MediaInfo:
    mime: str
    kind: str  # text / image / audio / video / pdf / office / archive / binary
    size: int
    width: int = 0
    height: int = 0

    @property
    def is_text(self) -> bool:
        return self.kind == KIND_TEXT

    @property
    def is_image(self) -> bool:
        return self.kind == "image"


def _riff_mime(head: bytes) -> str:
    # RIFF 是容器格式，真正类型在 8..12 字节。
    if len(head) < 12 or head[:4] != b"RIFF":
        return ""
    return {
        b"WAVE": "audio/wav",
        b"WEBP": "image/webp",
        b"AVI ": "video/x-msvideo",
    }.get(head[8:12], "application/octet-stream")


# mp4 家族里纯音频的 major brand：手机录音、Apple 语音备忘常见。
# 判成 video 会让附件链路去抽帧（必然抽不出来），所以这里必须分开。
_AUDIO_FTYP_BRANDS = frozenset({b"M4A ", b"M4B ", b"F4A ", b"F4B "})


def _ftyp_mime(head: bytes) -> str:
    # major brand 在 8..12 字节；其余 mp4 家族（含 .mov 的 "qt  "）仍按视频处理。
    if head[8:12] in _AUDIO_FTYP_BRANDS:
        return "audio/mp4"
    return "video/mp4"


def sniff_mime(head: bytes) -> str:
    """Return a MIME type from the file header, or "" when it looks like text."""
    if not head:
        return ""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if head.startswith(b"RIFF"):
        return _riff_mime(head)
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        # docx/xlsx/pptx 都是 zip，具体是哪一种交给调用方看扩展名。
        return "application/zip"
    if head.startswith(b"ID3"):
        return "audio/mpeg"
    if head.startswith(b"fLaC"):
        return "audio/flac"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"FORM") and head[8:12] == b"AIFF":
        return "audio/aiff"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return _ftyp_mime(head)
    if head.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "application/x-7z-compressed"
    if head.startswith(b"Rar!\x1a\x07"):
        return "application/x-rar-compressed"
    # 没有 ID3 头的 mp3：帧同步 11 个 1。
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    return ""


def kind_of(mime: str, suffix: str = "") -> str:
    """Map a MIME type (plus file suffix) to a coarse media kind."""
    if not mime:
        return "binary"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime == "application/pdf":
        return "pdf"
    if mime == "application/zip" and suffix.lower() in _OFFICE_SUFFIXES:
        return "office"
    if mime in (
        "application/zip",
        "application/gzip",
        "application/x-7z-compressed",
        "application/x-rar-compressed",
    ):
        return "archive"
    if mime == "application/octet-stream":
        return "binary"
    return "binary"


def looks_binary(head: bytes) -> bool:
    # 没有魔数时靠「不可打印控制字符比例」兜底；GBK 文件不会被误判成二进制。
    if not head:
        return False
    if b"\x00" in head:
        return True
    bad = sum(1 for b in head if b < 0x09 or 0x0E <= b < 0x20 or b == 0x7F)
    return bad / len(head) > 0.05


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i = 2
    n = len(data)
    while i + 9 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:  # 填充字节
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in _JPEG_SOF_MARKERS and i + 9 <= n:
            height = struct.unpack(">H", data[i + 5 : i + 7])[0]
            width = struct.unpack(">H", data[i + 7 : i + 9])[0]
            return width, height
        i += 2 + seg_len
    return 0, 0


def image_size(head: bytes) -> tuple[int, int]:
    """Best-effort (width, height) from a file header; (0, 0) when unknown."""
    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
        return struct.unpack(">II", head[16:24])
    if head.startswith((b"GIF87a", b"GIF89a")) and len(head) >= 10:
        return struct.unpack("<HH", head[6:10])
    if head.startswith(b"BM") and len(head) >= 26:
        width, height = struct.unpack("<ii", head[18:26])
        return abs(width), abs(height)
    if head.startswith(b"\xff\xd8"):
        return _jpeg_size(head)
    if len(head) >= 30 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        chunk = head[12:16]
        if chunk == b"VP8X":
            width = int.from_bytes(head[24:27], "little") + 1
            height = int.from_bytes(head[27:30], "little") + 1
            return width, height
        if chunk == b"VP8 ":
            width = int.from_bytes(head[26:28], "little") & 0x3FFF
            height = int.from_bytes(head[28:30], "little") & 0x3FFF
            return width, height
    return 0, 0


def probe(path: str | Path) -> MediaInfo:
    """Read the header of `path` and classify it. Raises OSError on unreadable files."""
    p = Path(path)
    size = p.stat().st_size
    with p.open("rb") as fh:
        head = fh.read(HEAD_BYTES)

    mime = sniff_mime(head)
    if not mime:
        if looks_binary(head):
            return MediaInfo("application/octet-stream", "binary", size)
        return MediaInfo("text/plain", KIND_TEXT, size)

    kind = kind_of(mime, p.suffix)
    if kind == "office":
        mime = _OFFICE_SUFFIXES[p.suffix.lower()]
    width, height = image_size(head) if kind == "image" else (0, 0)
    return MediaInfo(mime, kind, size, width, height)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


__all__ = [
    "HEAD_BYTES",
    "KIND_TEXT",
    "MediaInfo",
    "human_size",
    "image_size",
    "kind_of",
    "looks_binary",
    "probe",
    "sniff_mime",
]
