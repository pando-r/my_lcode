"""Edit a file via unique string replacement."""

from __future__ import annotations

import json
from pathlib import Path

from mewcode.tool import Result
from mewcode.tool.ctx import resolve_path


class EditFileTool:
    read_only: bool = False

    def name(self) -> str:
        return "edit_file"

    def description(self) -> str:
        return (
            "用 new_string 替换文件中唯一匹配的 old_string；old_string 必须唯一。"
            "编辑前请先用 read_file 读取目标文件，确认 old_string 唯一。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "old_string": {"type": "string", "description": "要替换的原文片段（须唯一匹配）"},
                "new_string": {"type": "string", "description": "替换后的新内容"},
            },
            "required": ["path", "old_string", "new_string"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)

        path = data.get("path")
        old = data.get("old_string")
        new = data.get("new_string")
        if not path or old is None or new is None:
            return Result(content="缺少参数: path / old_string / new_string", is_error=True)

        p = Path(resolve_path(path))
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            return Result(content=f"读取失败: {exc}", is_error=True)

        count = text.count(old)
        if count == 0:
            return Result(content="未找到匹配的内容", is_error=True)
        if count > 1:
            return Result(
                content=f"匹配到 {count} 处，old_string 不唯一，请提供更长上下文使其唯一",
                is_error=True,
            )

        try:
            p.write_text(text.replace(old, new, 1), encoding="utf-8")
        except OSError as exc:
            return Result(content=f"写入失败: {exc}", is_error=True)

        return Result(content=f"已替换 {path} 中的 1 处匹配")
