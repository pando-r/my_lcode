"""Execute the four hook action types."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx2 as httpx

from lcode.hook.rule import (
    ActionType,
    HttpAction,
    Payload,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)


@dataclass
class ExecutionResult:
    # blocked 仅对拦截类事件有意义；err 表示 hook 自身失败。
    blocked: bool = False
    reason: str = ""
    prompt: str = ""
    err: Exception | None = None


class Executor:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def run(self, rule: Rule, payload: Payload, *, blocking: bool) -> ExecutionResult:
        action = rule.action
        if action.type is ActionType.SHELL:
            assert action.shell is not None
            return await self._run_shell(action.shell, payload, blocking, rule.timeout_s)
        if action.type is ActionType.PROMPT:
            assert action.prompt is not None
            return self._run_prompt(action.prompt)
        if action.type is ActionType.HTTP:
            assert action.http is not None
            return await self._run_http(action.http, payload, blocking, rule.timeout_s)
        if action.type is ActionType.SUBAGENT:
            assert action.subagent is not None
            return self._run_subagent(action.subagent)
        return ExecutionResult(err=RuntimeError(f"unknown action type: {action.type}"))

    # ── shell ────────────────────────────────────────────────────────────

    async def _run_shell(
        self,
        action: ShellAction,
        payload: Payload,
        blocking: bool,
        timeout_s: float,
    ) -> ExecutionResult:
        try:
            proc = await _spawn_shell(action.command)
        except OSError as exc:
            return ExecutionResult(err=exc)

        data = _marshal_sorted(payload)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(input=data), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecutionResult(err=TimeoutError(f"timed out after {timeout_s:g}s"))

        code = proc.returncode or 0
        err_text = stderr.decode("utf-8", errors="replace")
        out_text = stdout.decode("utf-8", errors="replace")

        if blocking and code == 2:
            reason = (err_text or out_text).rstrip("\r\n")
            return ExecutionResult(blocked=True, reason=reason)
        if code == 0:
            return ExecutionResult()
        return ExecutionResult(err=RuntimeError(f"exit {code}: {err_text or out_text}"))

    # ── prompt / subagent ────────────────────────────────────────────────

    def _run_prompt(self, action: PromptAction) -> ExecutionResult:
        return ExecutionResult(prompt=action.text)

    def _run_subagent(self, action: SubagentAction) -> ExecutionResult:
        print(
            f"[hook subagent] not yet implemented, skipped: {action.agent_name}",
            file=sys.stderr,
        )
        return ExecutionResult()

    # ── http ─────────────────────────────────────────────────────────────

    async def _run_http(
        self,
        action: HttpAction,
        payload: Payload,
        blocking: bool,
        timeout_s: float,
    ) -> ExecutionResult:
        try:
            body = (
                action.body.format_map(payload)
                if action.body is not None
                else json.dumps(payload, sort_keys=True, ensure_ascii=False)
            )
        except (KeyError, ValueError, IndexError) as exc:
            return ExecutionResult(err=RuntimeError(f"body template failed: {exc}"))

        try:
            response = await self._client.request(
                action.method or "POST",
                action.url,
                content=body,
                headers=action.headers,
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            return ExecutionResult(err=exc)
        except httpx.HTTPError as exc:
            return ExecutionResult(err=exc)

        if response.status_code >= 400:
            return ExecutionResult(
                err=RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
            )
        if not blocking:
            return ExecutionResult()

        try:
            data = json.loads(response.text or "{}")
        except json.JSONDecodeError as exc:
            return ExecutionResult(err=RuntimeError(f"invalid JSON response: {exc}"))
        if isinstance(data, dict) and data.get("decision") == "block":
            return ExecutionResult(blocked=True, reason=str(data.get("reason") or ""))
        return ExecutionResult()


def _marshal_sorted(payload: Payload) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


async def _spawn_shell(command: str):
    # POSIX 直接用 sh -c；Windows 上优先找 Git Bash 的 sh，保持同样的 hook 语法。
    sh = _find_sh()
    if sh is not None:
        return await asyncio.create_subprocess_exec(
            sh,
            "-c",
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    return await asyncio.create_subprocess_shell(
        command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


@lru_cache(maxsize=1)
def _find_sh() -> str | None:
    if os.name != "nt":
        found = shutil.which("sh")
        return found
    found = shutil.which("sh")
    if found:
        return found
    candidates = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "sh.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Git"
        / "usr"
        / "bin"
        / "sh.exe",
    )
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


__all__ = ["ExecutionResult", "Executor"]
