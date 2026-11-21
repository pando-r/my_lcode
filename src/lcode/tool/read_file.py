"""Read a text file with line numbers."""

from __future__ import annotations

import json
from pathlib import Path

from lcode.tool import Result, _truncate
from lcode.tool.ctx import resolve_path
from lcode.tool.media import MediaInfo, human_size, probe

MAX_LINES = 2000
MAX_CHARS = 256 * 1024
# 只读头部就够填满 MAX_LINES/MAX_CHARS；4MB 上限避免把 1GB 日志整个读进内存。
MAX_BYTES = 4 * 1024 * 1024

# 非文本文件不回内容，只回一句「这是什么 + 该怎么读」。
_KIND_HINTS = {
    "image": "要看懂这张图，请调用 view_image 工具（需要在 .lcode/config.yaml 里配置 vision 段）。",
    "audio": "音频不能按文本读：请调用 transcribe_audio 工具转写成文字后再阅读。",
    "video": "视频不能按文本读：调用 transcribe_audio 抽音轨转写，或先用 ffmpeg 抽关键帧后 view_image。",
    "pdf": "PDF 需要先抽取文本层（例如 pypdf）；扫描件则转成图片后用 view_image。",
    "office": "Office 文档是压缩包结构，需要先用 python-docx / openpyxl 之类抽取文本。",
    "archive": "压缩包不自动解压：先确认内容再解压到临时目录。",
    "binary": "二进制文件不返回内容；需要看结构可以用 bash 里的 xxd / Format-Hex。",
}


class ReadFileTool:
    read_only: bool = True

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return (
            "读取指定路径的文本文件内容（带行号）；文件不存在或不可读时返回错误。"
            "图片/音频/PDF 等非文本文件只返回类型提示，不返回二进制内容。"
        )

    # 参数定义
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径"},
            },
            "required": ["path"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)

        path = data.get("path")
        if not path:
            return Result(content="缺少参数: path", is_error=True)

        p = Path(resolve_path(path))
        if p.is_dir():
            return Result(content=f"是目录，无法读取: {path}", is_error=True)
        if not p.is_file():
            return Result(content=f"文件不存在: {path}", is_error=True)

        # 先嗅探文件头：非文本直接拒绝，避免把二进制当 UTF-8 解出满屏乱码。
        try:
            info = probe(p)
        except OSError as exc:
            return Result(content=f"读取失败: {exc}", is_error=True)
        if not info.is_text:
            return Result(content=_non_text_message(path, info), is_error=True)

        try:
            text, truncated = _read_text(p)
        except OSError as exc:
            return Result(content=f"读取失败: {exc}", is_error=True)

        numbered = "\n".join(f"{i:6d}\t{line}" for i, line in enumerate(text.splitlines(), 1))
        content = _truncate(numbered, MAX_LINES, MAX_CHARS)
        if truncated:
            content += f"\n[文件大于 {human_size(MAX_BYTES)}，只读取了开头部分]"
        return Result(content=content)


def _read_text(p: Path) -> tuple[str, bool]:
    # 编码回退：utf-8（含 BOM）→ gb18030（覆盖 GBK/GB2312）→ 带替换的 utf-8。
    with p.open("rb") as fh:
        raw = fh.read(MAX_BYTES + 1)
    truncated = len(raw) > MAX_BYTES
    raw = raw[:MAX_BYTES]
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding), truncated
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), truncated


def _non_text_message(path: str, info: MediaInfo) -> str:
    lines = [
        f"这不是文本文件：{path}",
        f"类型: {info.mime}（{info.kind}），大小: {human_size(info.size)}",
    ]
    if info.width and info.height:
        lines.append(f"尺寸: {info.width}x{info.height}")
    hint = _KIND_HINTS.get(info.kind)
    if hint:
        lines.append("")
        lines.append(hint)
    return "\n".join(lines)
