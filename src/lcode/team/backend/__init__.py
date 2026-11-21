"""Execution backends for team members."""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from lcode.team.types import BackendType


@dataclass
class SpawnRequest:
    team_name: str
    member_name: str
    agent_id: str
    worktree_path: str
    session_dir: str
    agent_type: str = ""
    model: str = ""
    initial_prompt: str = ""
    plan_mode_required: bool = False
    # in-process 专用；Pane 后端忽略。
    sub_agent: Any = None
    conv: Any = None
    task_mgr: Any = None


class Backend(Protocol):
    def type(self) -> BackendType: ...

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]: ...

    async def wake(self, pane_id: str, agent_id: str) -> None: ...

    async def kill(self, pane_id: str, agent_id: str) -> None: ...


def build_member_command(req: SpawnRequest) -> str:
    # pane 子进程走 --team-member 自治循环；任务本身预写入 mailbox。
    argv = [
        sys.executable,
        "-m",
        "lcode",
        "--team-member",
        "--team",
        req.team_name,
        "--member",
        req.member_name,
        "--agent-id",
        req.agent_id,
        "--session-dir",
        req.session_dir,
        "--worktree",
        req.worktree_path,
    ]
    if req.agent_type:
        argv.extend(["--agent-type", req.agent_type])
    if req.model:
        argv.extend(["--model", req.model])
    if req.plan_mode_required:
        argv.append("--plan-mode")
    return shlex.join(argv)


def new_backend(backend_type: BackendType, **deps: Any) -> Backend:
    if backend_type is BackendType.TMUX:
        from lcode.team.backend.tmux import TmuxBackend

        return TmuxBackend(cwd=deps.get("cwd", ""))
    if backend_type is BackendType.ITERM2:
        from lcode.team.backend.iterm2 import Iterm2Backend

        return Iterm2Backend(cwd=deps.get("cwd", ""))
    from lcode.team.backend.inprocess import InProcessBackend

    return InProcessBackend(task_mgr=deps["task_mgr"])


__all__ = ["Backend", "SpawnRequest", "build_member_command", "new_backend"]
