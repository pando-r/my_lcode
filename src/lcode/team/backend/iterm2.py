"""iTerm2 pane backend (macOS only)."""

from __future__ import annotations

import asyncio
import contextlib

from lcode.team.backend import SpawnRequest, build_member_command
from lcode.team.types import BackendType


async def _run_it2(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "it2",
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())
    return stdout.decode("utf-8", errors="replace").strip()


class Iterm2Backend:
    def __init__(self, cwd: str = "") -> None:
        self._cwd = cwd

    def type(self) -> BackendType:
        return BackendType.ITERM2

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        output = await _run_it2("split", "--new-pane", "--command", build_member_command(req))
        pane_id = output.splitlines()[-1].strip() if output else ""
        return pane_id, req.agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        with contextlib.suppress(RuntimeError):
            await _run_it2("send-text", "--pane", pane_id, "")

    async def kill(self, pane_id: str, agent_id: str) -> None:
        with contextlib.suppress(RuntimeError):
            await _run_it2("close-pane", "--pane", pane_id)


__all__ = ["Iterm2Backend"]
