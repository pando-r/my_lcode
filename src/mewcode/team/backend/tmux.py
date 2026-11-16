"""tmux pane backend."""

from __future__ import annotations

import asyncio
import contextlib
import os

from mewcode.team.backend import SpawnRequest, build_member_command
from mewcode.team.types import BackendType


async def _run_tmux(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())
    return stdout.decode("utf-8", errors="replace").strip()


class TmuxBackend:
    def __init__(self, cwd: str = "") -> None:
        self._cwd = cwd

    def type(self) -> BackendType:
        return BackendType.TMUX

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        command = build_member_command(req)
        if os.environ.get("TMUX"):
            args = [
                "split-window",
                "-h",
                "-P",
                "-F",
                "#{pane_id}",
                "--",
                command,
            ]
        else:
            args = [
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "--",
                command,
            ]
        output = await _run_tmux(*args)
        pane_id = output.splitlines()[-1].strip() if output else ""
        return pane_id, req.agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        with contextlib.suppress(RuntimeError):
            await _run_tmux("send-keys", "-t", pane_id, "", "Enter")

    async def kill(self, pane_id: str, agent_id: str) -> None:
        with contextlib.suppress(RuntimeError):
            await _run_tmux("kill-pane", "-t", pane_id)


__all__ = ["TmuxBackend"]
