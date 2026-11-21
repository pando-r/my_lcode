"""Execute a shell command."""

from __future__ import annotations

import asyncio
import json

from lcode.tool import Result, _truncate
from lcode.tool.ctx import resolve_path

MAX_LINES = 10000
MAX_CHARS = 30000


class BashTool:
    read_only: bool = False

    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return (
            "在工作目录下执行 shell 命令，返回 stdout、stderr 与退出码。"
            "读文件、找文件、搜内容请优先用 read_file / glob / grep，不要用 bash 拼凑。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
            },
            "required": ["command"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)

        command = data.get("command")
        if not command:
            return Result(content="缺少参数: command", is_error=True)
        # 启动子进程执行命令
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=resolve_path(""),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return Result(content=f"命令执行失败: {exc}", is_error=True)
        # 等待子进程执行完成
        try:
            stdout_b, stderr_b = await proc.communicate()
        except asyncio.CancelledError:
            proc.kill()
            await proc.communicate()
            raise
        # 解码子进程输出
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        # 格式化输出
        out = f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        return Result(content=_truncate(out, MAX_LINES, MAX_CHARS))
