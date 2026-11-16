"""Team data model and errors."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class BackendType(StrEnum):
    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"


class TeamError(Exception):
    pass


class TeamNotFoundError(TeamError):
    pass


class TeamHasActiveMembersError(TeamError):
    pass


class MemberExistsError(TeamError):
    pass


class MemberNotFoundError(TeamError):
    pass


class InProcessTeammateNoSpawnError(TeamError):
    pass


@dataclass
class TeammateInfo:
    name: str
    agent_id: str
    agent_type: str = ""
    model: str = ""
    worktree_path: str = ""
    branch: str = ""
    backend_type: BackendType = BackendType.IN_PROCESS
    pane_id: str = ""
    is_active: bool | None = None
    plan_mode_required: bool = False
    session_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "model": self.model,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "backend_type": str(self.backend_type),
            "pane_id": self.pane_id,
            "is_active": self.is_active,
            "plan_mode_required": self.plan_mode_required,
            "session_dir": self.session_dir,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeammateInfo:
        return cls(
            name=str(data.get("name", "")),
            agent_id=str(data.get("agent_id", "")),
            agent_type=str(data.get("agent_type", "")),
            model=str(data.get("model", "")),
            worktree_path=str(data.get("worktree_path", "")),
            branch=str(data.get("branch", "")),
            backend_type=BackendType(data.get("backend_type", BackendType.IN_PROCESS)),
            pane_id=str(data.get("pane_id", "")),
            is_active=data.get("is_active"),
            plan_mode_required=bool(data.get("plan_mode_required", False)),
            session_dir=str(data.get("session_dir", "")),
        )


@dataclass
class Team:
    name: str
    sanitized_name: str
    lead_agent_id: str = "lead"
    backend: BackendType = BackendType.IN_PROCESS
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    members: list[TeammateInfo] = field(default_factory=list)

    config_dir: str = ""
    config_path: str = ""
    tasks_path: str = ""
    mailbox_dir: str = ""

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def member_by_name(self, name: str) -> TeammateInfo | None:
        return next((member for member in self.members if member.name == name), None)

    def member_by_agent_id(self, agent_id: str) -> TeammateInfo | None:
        return next((member for member in self.members if member.agent_id == agent_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sanitized_name": self.sanitized_name,
            "lead_agent_id": self.lead_agent_id,
            "backend": str(self.backend),
            "description": self.description,
            "created_at": int(self.created_at.timestamp()),
            "members": [member.to_dict() for member in self.members],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], config_dir: str = "") -> Team:
        created_raw = data.get("created_at", 0)
        try:
            created = datetime.fromtimestamp(float(created_raw))
        except (TypeError, ValueError, OSError):
            created = datetime.now()
        team = cls(
            name=str(data.get("name", "")),
            sanitized_name=str(data.get("sanitized_name", "")),
            lead_agent_id=str(data.get("lead_agent_id", "lead")),
            backend=BackendType(data.get("backend", BackendType.IN_PROCESS)),
            description=str(data.get("description", "")),
            created_at=created,
            members=[TeammateInfo.from_dict(item) for item in data.get("members", [])],
        )
        if config_dir:
            team.bind_paths(config_dir)
        return team

    async def add_member(self, info: TeammateInfo) -> None:
        # 加锁后先从磁盘 reload，避免跨进程丢更新（F19c）。
        from mewcode.team.persistence import reload_from_disk_locked, save_team

        async with self._lock:
            await reload_from_disk_locked(self)
            if self.member_by_name(info.name) is not None:
                raise MemberExistsError(f"成员已存在: {info.name}")
            self.members.append(info)
            save_team(self)

    async def set_member_active(self, name: str, active: bool) -> None:
        from mewcode.team.persistence import reload_from_disk_locked, save_team

        async with self._lock:
            await reload_from_disk_locked(self)
            member = self.member_by_name(name)
            if member is None:
                raise MemberNotFoundError(f"成员不存在: {name}")
            member.is_active = active
            save_team(self)

    async def update_member(self, info: TeammateInfo, *, preserve_active: bool = False) -> None:
        from mewcode.team.persistence import reload_from_disk_locked, save_team

        async with self._lock:
            await reload_from_disk_locked(self)
            for index, member in enumerate(self.members):
                if member.name == info.name:
                    if preserve_active:
                        info.is_active = member.is_active
                    self.members[index] = info
                    save_team(self)
                    return
            raise MemberNotFoundError(f"成员不存在: {info.name}")

    async def remove_member(self, name: str) -> None:
        from mewcode.team.persistence import reload_from_disk_locked, save_team

        async with self._lock:
            await reload_from_disk_locked(self)
            before = len(self.members)
            self.members = [m for m in self.members if m.name != name]
            if len(self.members) == before:
                raise MemberNotFoundError(f"成员不存在: {name}")
            save_team(self)

    def bind_paths(self, config_dir: str) -> None:
        from pathlib import Path

        directory = Path(config_dir)
        self.config_dir = str(directory)
        self.config_path = str(directory / "config.json")
        self.tasks_path = str(directory / "tasks.json")
        self.mailbox_dir = str(directory / "mailbox")


__all__ = [
    "BackendType",
    "InProcessTeammateNoSpawnError",
    "MemberExistsError",
    "MemberNotFoundError",
    "Team",
    "TeamError",
    "TeamHasActiveMembersError",
    "TeamNotFoundError",
    "TeammateInfo",
]
