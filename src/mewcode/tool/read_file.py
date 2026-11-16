"""Read a file with line numbers."""

from __future__ import annotations

import json
from pathlib import Path

from mewcode.tool import Result, _truncate
from mewcode.tool.ctx import resolve_path

MAX_LINES = 2000
MAX_CHARS = 256 * 1024


class ReadFileTool:
    read_only: bool = True

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "读取指定路径的文本文件内容（带行号）；文件不存在或不可读时返回错误。"

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

        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return Result(content=f"读取失败: {exc}", is_error=True)

        numbered = "\n".join(f"{i:6d}\t{line}" for i, line in enumerate(text.splitlines(), 1))
        return Result(content=_truncate(numbered, MAX_LINES, MAX_CHARS))
