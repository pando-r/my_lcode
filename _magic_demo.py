import struct
import sys
import zlib

sys.path.insert(0, "src")
from lcode.tool.media import image_size, sniff_mime


def png(w: int, h: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + b"\x00" * w * 3 for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


data = png(37, 21)
print("first 16 bytes :", data[:16].hex(" "))
print("first 8  bytes :", data[:8])
print("sniff_mime ->", sniff_mime(data[:8192]))
print("image_size ->", image_size(data[:8192]))
