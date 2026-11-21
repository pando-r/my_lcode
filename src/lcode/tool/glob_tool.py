"""Find files by glob pattern."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from lcode.tool import Result
from lcode.tool.ctx import resolve_path

MAX_RESULTS = 100


class GlobTool:
    read_only: bool = True

    def name(self) -> str:
        return "glob"

    def description(self) -> str:
        return "按 glob 模式查找匹配的文件路径（如 **/*.py），返回文件路径列表。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
                "path": {"type": "string", "description": "搜索根目录，默认当前目录"},
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

        root = Path(resolve_path(data.get("path") or "."))
        matches: list[str] = []
        for i, p in enumerate(sorted(root.glob(pattern))):
            if p.is_file():
                matches.append(str(p.relative_to(root)))
            if i % 100 == 99:
                await asyncio.sleep(0)

        if not matches:
            return Result(content="无匹配")

        if len(matches) > MAX_RESULTS:
            matches = matches[:MAX_RESULTS]
            matches.append(f"[truncated: 仅显示前 {MAX_RESULTS} 条]")
        return Result(content="\n".join(matches))
