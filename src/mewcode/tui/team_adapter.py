"""Adapt team.Manager to the command layer's TeamAccessor protocol."""

from __future__ import annotations

from mewcode.command.ui import TeamMemberSummary, TeamSummary
from mewcode.team import Manager, Store
from mewcode.team.tasks import Filter


class TeamAdapter:
    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    def list(self) -> list[TeamSummary]:
        return [
            TeamSummary(
                name=team.sanitized_name,
                backend=str(team.backend),
                member_count=len(team.members),
                active_count=sum(
                    1
                    for member in team.members
                    if member.name != "lead" and member.is_active is not False
                ),
                config_path=team.config_path,
            )
            for team in self._manager.list_()
        ]

    async def info(self, name: str) -> tuple[TeamSummary, list[TeamMemberSummary]]:
        team = self._manager.get(name)
        if team is None:
            raise ValueError(f"未知 Team: {name}")
        from mewcode.team.persistence import reload_from_disk_locked

        async with team._lock:
            await reload_from_disk_locked(team)
        tasks = await Store(team.tasks_path).list_(Filter())
        members = []
        for member in team.members:
            members.append(
                TeamMemberSummary(
                    name=member.name,
                    agent_id=member.agent_id,
                    backend=str(member.backend_type),
                    worktree_path=member.worktree_path,
                    is_active=member.is_active,
                    task_count=sum(1 for task in tasks if task.assignee == member.name),
                )
            )
        summary = next(
            (item for item in self.list() if item.name == team.sanitized_name),
            TeamSummary(team.sanitized_name, str(team.backend), 0, 0),
        )
        return summary, members

    async def delete(self, name: str, force: bool) -> None:
        await self._manager.delete(name, force)

    async def kill(self, member: str) -> str:
        for team in self._manager.list_():
            target = team.member_by_name(member)
            if target is None or target.name == "lead":
                continue
            try:
                await self._manager.backend_for(target).kill(target.pane_id, target.agent_id)
            finally:
                self._manager.registry.unregister(target.name)
                await team.remove_member(target.name)
            return team.sanitized_name
        raise ValueError(f"找不到队员: {member}")


__all__ = ["TeamAdapter"]
