"""In-process backend: teammates run as asyncio tasks in the Lead's loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mewcode.team.backend import SpawnRequest
from mewcode.team.types import BackendType

if TYPE_CHECKING:
    from mewcode.task import Manager as TaskManager


class InProcessBackend:
    def __init__(self, task_mgr: TaskManager) -> None:
        self._task_mgr = task_mgr

    def type(self) -> BackendType:
        return BackendType.IN_PROCESS

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        agent_id = await self._task_mgr.launch(
            req.sub_agent,
            req.conv,
            req.member_name,
            req.initial_prompt,
            task_id=req.agent_id,
            cwd=req.worktree_path,
        )
        return "", agent_id

    async def wake(self, pane_id: str, agent_id: str) -> None:
        return None

    async def kill(self, pane_id: str, agent_id: str) -> None:
        await self._task_mgr.stop(agent_id)


__all__ = ["InProcessBackend"]
