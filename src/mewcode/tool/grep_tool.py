"""Search code content by regex."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from mewcode.tool import Result
from mewcode.tool.ctx import resolve_path

MAX_HITS = 100
MAX_LINE = 1024 * 1024


class GrepTool:
    read_only: bool = True

    def name(self) -> str:
        return "grep"

    def description(self) -> str:
        return "在文件内容中按 Python 正则搜索，返回 file:line:content 命中列表。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python 正则表达式"},
                "path": {"type": "string", "description": "搜索根目录，默认当前目录"},
                "glob": {"type": "string", "description": "可选文件名过滤 glob"},
            },
            "required": ["pattern"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)

        pattern = data.get("pattern")
        if not pattern:
            return Result(content="缺少参数: pattern", is_error=True)

        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return Result(content=f"正则非法: {exc}", is_error=True)

        root = Path(resolve_path(data.get("path") or "."))
        glob_filter = data.get("glob")
        iterator = root.rglob(glob_filter) if glob_filter else root.rglob("*")

        hits: list[str] = []
        truncated = False
        for file_path in iterator:
            if not file_path.is_file():
                continue
            shown = file_path.relative_to(root)
            try:
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if len(line) > MAX_LINE:
                            hits.append(f"{shown}:{lineno}: [该行过长，未完整搜索]")
                            continue
                        if rx.search(line):
                            hits.append(f"{shown}:{lineno}:{line.rstrip()}")
                            if len(hits) >= MAX_HITS:
                                truncated = True
                                break
            except OSError:
                continue
            if truncated:
                break
            await asyncio.sleep(0)

        if not hits:
            return Result(content="无命中")

        out = "\n".join(hits)
        if truncated:
            out += "\n[truncated]"
        return Result(content=out)
