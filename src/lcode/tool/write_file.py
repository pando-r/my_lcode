"""Write a file (overwrite), creating parent directories as needed."""

from __future__ import annotations

import json
from pathlib import Path

from lcode.tool import Result
from lcode.tool.ctx import resolve_path


class WriteFileTool:
    read_only: bool = False

    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "写入（覆盖）指定路径的文件；父目录不存在时自动创建。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "要写入的完整内容"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)

        path = data.get("path")
        content = data.get("content")
        if not path or content is None:
            return Result(content="缺少参数: path / content", is_error=True)

        p = Path(resolve_path(path))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except OSError as exc:
            return Result(content=f"写入失败: {exc}", is_error=True)

        return Result(content=f"已写入 {path}（{len(content.encode('utf-8'))} 字节）")
