"""Use a vision model as Lcode's eyes: turn an image into text.

主模型（如 DeepSeek）不认 image_url，所以图片不能直接进对话历史。
这里的做法是「工具式多模态」：让一个带视觉能力的副模型看图，
只把答案（文字）交回主模型。对话历史里因此始终只有文本，
JSONL 落盘、上下文压缩、子 Agent 复用都不用改。
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from lcode.config import VisionConfig
from lcode.tool.media import HEAD_BYTES, human_size, image_size, sniff_mime

SUPPORTED_MIME = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
# 单图上限：多数视觉 API 的硬限制在 5MB 左右，超了直接 400。
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# 多图总上限：DashScope 域名的请求体上限是 16 MiB，而 base64 会膨胀 4/3，
# 6 张 5MB 的图会变成 40MB，必然被拒。10MB 原始字节 → 13.3MB base64，
# 加 JSON 开销后仍稳在 16 MiB 以内，对 OpenAI/Anthropic 也更宽松。
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 6
MAX_ANSWER_CHARS = 12000

_SYSTEM_PROMPT = (
    "You are a careful image observer for a coding agent that cannot see.\n"
    "- Answer ONLY what was asked; skip generic description.\n"
    "- Lead with the conclusion, then the evidence (exact text, coordinates, colors).\n"
    "- When reading code or error messages, quote them verbatim; never paraphrase identifiers.\n"
    "- If something is unreadable or you are unsure, say so explicitly.\n"
    "- Reply in the same language as the question. Keep it under 300 words unless asked for more."
)

_SUFFIX_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class VisionError(Exception):
    """Raised when an image cannot be prepared or the vision model fails."""


@dataclass
class ImagePayload:
    path: str
    mime: str
    data: bytes
    width: int = 0
    height: int = 0
    note: str = ""

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    def estimate_tokens(self) -> int:
        # 与 Anthropic 的 w*h/750 一致；OpenAI 按 tile 计费也在同一量级。
        if not (self.width and self.height):
            return 1500
        return max(85, int(self.width * self.height / 750))

    def summary(self) -> str:
        name = Path(self.path).name
        dims = f"{self.width}x{self.height}" if self.width else "尺寸未知"
        return (
            f"[{name} {dims} {self.mime} {human_size(len(self.data))} "
            f"≈{self.estimate_tokens()} tok]"
        )


def load_image(
    path: str | Path, max_side: int = 1568, crop: list[int] | None = None
) -> ImagePayload:
    """Read and (if needed) resize/crop an image into a payload ready for the API."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise VisionError(f"读取图片失败: {path}（{exc}）") from exc

    mime = sniff_mime(raw[:HEAD_BYTES]) or _SUFFIX_MIME.get(p.suffix.lower(), "")
    if mime not in SUPPORTED_MIME:
        raise VisionError(f"不支持的图片格式: {path}（{mime or '未知类型'}）")

    width, height = image_size(raw[:HEAD_BYTES])
    need_work = bool(crop) or max(width, height) > max_side or len(raw) > MAX_IMAGE_BYTES
    if not need_work:
        return ImagePayload(str(p), mime, raw, width, height)
    return _prepare_with_pillow(p, raw, mime, max_side, crop)


def _prepare_with_pillow(
    p: Path,
    raw: bytes,
    mime: str,
    max_side: int,
    crop: list[int] | None,
) -> ImagePayload:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - 取决于安装方式
        raise VisionError(
            "这张图需要缩放/裁剪才能发送，但当前环境没有 Pillow。"
            "请安装：pip install lcode[media]"
        ) from exc

    notes: list[str] = []
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        width, height = im.size
        if crop:
            box = _crop_box(crop, width, height)
            im = im.crop(box)
            notes.append(f"已裁剪到 {box[2] - box[0]}x{box[3] - box[1]}")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
            notes.append(f"已缩放到 {im.size[0]}x{im.size[1]}")
        data, out_mime = _encode(im)
        out_width, out_height = im.size

    if len(data) > MAX_IMAGE_BYTES:
        raise VisionError(
            f"图片处理后仍有 {human_size(len(data))}，超过 {human_size(MAX_IMAGE_BYTES)} 上限；"
            "请先裁剪或降低分辨率。"
        )
    return ImagePayload(str(p), out_mime, data, out_width, out_height, "；".join(notes))


def _crop_box(crop: list[int], width: int, height: int) -> tuple[int, int, int, int]:
    if len(crop) != 4 or not all(isinstance(v, int) for v in crop):
        raise VisionError("crop 必须是 4 个整数：[x1, y1, x2, y2]")
    x1, y1, x2, y2 = crop
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise VisionError(f"crop 超出图片范围：图片是 {width}x{height}，收到 {crop}")
    return x1, y1, x2, y2


def _encode(im) -> tuple[bytes, str]:
    buf = io.BytesIO()
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if has_alpha:
        # 保留透明通道改存 PNG：JPEG 没有 alpha，截图里的半透明会被压成黑块。
        im.convert("RGBA").save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    im.convert("RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


def total_bytes(payloads: list[ImagePayload]) -> int:
    return sum(len(p.data) for p in payloads)


def check_total_size(payloads: list[ImagePayload]) -> None:
    """Reject a batch whose base64 body would exceed the provider's request limit."""
    total = total_bytes(payloads)
    if total <= MAX_TOTAL_BYTES:
        return
    raise VisionError(
        f"这批图一共 {human_size(total)}，超过单次请求上限 {human_size(MAX_TOTAL_BYTES)}"
        f"（base64 后会再膨胀 1/3）。请减少张数、先裁剪，或用 max_side 调小分辨率。"
    )


async def ask_vision(
    cfg: VisionConfig,
    payloads: list[ImagePayload],
    question: str,
    detail: str = "high",
) -> str:
    """Send images plus a question to the vision model and return its text answer."""
    if not payloads:
        raise VisionError("没有可发送的图片")
    if not cfg.ready():
        raise VisionError("vision 未配置：请在 .lcode/config.yaml 里写 api_key 与 model")
    check_total_size(payloads)
    if cfg.protocol == "anthropic":
        return await _ask_anthropic(cfg, payloads, question)
    return await _ask_openai(cfg, payloads, question, detail)


async def _ask_openai(
    cfg: VisionConfig, payloads: list[ImagePayload], question: str, detail: str
) -> str:
    import openai

    client = openai.AsyncOpenAI(
        api_key=cfg.api_key,
        base_url=cfg.base_url or None,
        timeout=cfg.timeout,
    )
    content: list[dict] = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{p.mime};base64,{p.b64}", "detail": detail},
        }
        for p in payloads
    ]
    content.append({"type": "text", "text": question})
    try:
        resp = await client.chat.completions.create(
            model=cfg.model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        )
    except Exception as exc:  # noqa: BLE001 - 统一转成可读的失败原因
        raise VisionError(f"视觉模型请求失败: {exc}") from exc
    choice = resp.choices[0] if resp.choices else None
    text = (choice.message.content if choice and choice.message else "") or ""
    return text.strip()


async def _ask_anthropic(
    cfg: VisionConfig, payloads: list[ImagePayload], question: str
) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(
        api_key=cfg.api_key,
        base_url=cfg.base_url or None,
        timeout=cfg.timeout,
    )
    blocks: list[dict] = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": p.mime, "data": p.b64},
        }
        for p in payloads
    ]
    blocks.append({"type": "text", "text": question})
    try:
        msg = await client.messages.create(
            model=cfg.model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": blocks}],
        )
    except Exception as exc:  # noqa: BLE001
        raise VisionError(f"视觉模型请求失败: {exc}") from exc
    return "".join(
        block.text for block in msg.content if getattr(block, "type", None) == "text"
    ).strip()


__all__ = [
    "MAX_IMAGES",
    "MAX_ANSWER_CHARS",
    "MAX_IMAGE_BYTES",
    "MAX_TOTAL_BYTES",
    "SUPPORTED_MIME",
    "ImagePayload",
    "VisionError",
    "ask_vision",
    "check_total_size",
    "load_image",
    "total_bytes",
]
