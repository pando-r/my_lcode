"""Team manager: create/get/delete teams and coordinate teammates."""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lcode.team.backend.detect import detect
from lcode.team.mailbox import Box, Message, MessageType
from lcode.team.persistence import read_json, sanitize, save_team
from lcode.team.registry import AgentNameRegistry
from lcode.team.types import (
    BackendType,
    Team,
    TeamError,
    TeamHasActiveMembersError,
    TeammateInfo,
    TeamNotFoundError,
)


@dataclass
class LeadMessage:
    team_name: str
    from_: str
    type: str
    content: str
    timestamp: str


class Manager:
    def __init__(
        self,
        home_dir: str | Path,
        project_root: str | Path,
        wt_mgr: Any = None,
        task_mgr: Any = None,
        name_reg: AgentNameRegistry | None = None,
        catalog: Any = None,
        fork_teammate_enabled: bool = False,
    ) -> None:
        self.home_dir = Path(home_dir)
        self.project_root = str(Path(project_root).resolve())
        self.teams_base = self.home_dir / ".lcode" / "teams"
        self.teams_base.mkdir(parents=True, exist_ok=True)
        self.wt_mgr = wt_mgr
        self.task_mgr = task_mgr
        self.registry = name_reg or AgentNameRegistry()
        self.catalog = catalog
        self.fork_teammate_enabled = fork_teammate_enabled
        self.teams: dict[str, Team] = {}
        self._member_writers: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._active_name = ""
        self._scan()

    def _scan(self) -> None:
        latest: Team | None = None
        for directory in sorted(self.teams_base.iterdir()):
            if not directory.is_dir():
                continue
            config = directory / "config.json"
            if not config.is_file():
                continue
            try:
                data = read_json(config)
                team = Team.from_dict(data, str(directory))
            except Exception as exc:  # noqa: BLE001 - one bad team must not block start
                print(f"team: 跳过损坏的 {config}: {exc}", file=sys.stderr)
                continue
            if not team.sanitized_name:
                team.sanitized_name = directory.name
            self.teams[team.sanitized_name] = team
            if latest is None or team.created_at > latest.created_at:
                latest = team
        if latest is not None:
            self._active_name = latest.sanitized_name

    # ── queries ──────────────────────────────────────────────────────────

    def get(self, name: str) -> Team | None:
        return self.teams.get(name) or self.teams.get(sanitize(name))

    def list_(self) -> list[Team]:
        return sorted(self.teams.values(), key=lambda team: team.created_at)

    def list(self) -> list[Team]:
        return self.list_()

    def active_team(self) -> Team | None:
        return self.teams.get(self._active_name)

    def set_active(self, name: str) -> None:
        self._active_name = name

    def is_teammate_context(self, ctx: Any) -> tuple[str, str, bool]:
        teammate = getattr(getattr(ctx, "agent", None), "teammate_context", None)
        if teammate is None:
            return "", "", False
        return (
            teammate.team_name,
            teammate.member_name,
            teammate.backend_type == str(BackendType.IN_PROCESS),
        )

    # ── lifecycle ────────────────────────────────────────────────────────

    async def create(self, name: str, description: str = "", agent_type: str = "") -> Team:
        sanitized = sanitize(name)
        if not sanitized:
            raise ValueError("Team 名称不能为空")
        async with self._lock:
            unique = sanitized
            counter = 2
            while unique in self.teams:
                unique = f"{sanitized}-{counter}"
                counter += 1
            config_dir = self.teams_base / unique
            config_dir.mkdir(parents=True, exist_ok=False)
            try:
                (config_dir / "mailbox").mkdir(parents=True, exist_ok=True)
                team = Team(
                    name=name,
                    sanitized_name=unique,
                    backend=detect(),
                    description=description,
                )
                team.bind_paths(str(config_dir))
                team.members.append(
                    TeammateInfo(name="lead", agent_id=team.lead_agent_id, is_active=None)
                )
                save_team(team)
            except Exception:
                shutil.rmtree(config_dir, ignore_errors=True)
                raise
            self.teams[unique] = team
            self._active_name = unique
            return team

    async def delete(self, name: str, force: bool = False) -> None:
        async with self._lock:
            team = self.get(name)
            if team is None:
                raise TeamNotFoundError(f"未知 Team: {name}")
            from lcode.team.persistence import reload_from_disk_locked

            async with team._lock:
                await reload_from_disk_locked(team)
            if not force:
                active = [
                    member
                    for member in team.members
                    if member.name != "lead" and member.is_active is not False
                ]
                if active:
                    raise TeamHasActiveMembersError(
                        f"Team {team.sanitized_name} 仍有活跃成员: "
                        + ", ".join(member.name for member in active)
                    )
            # 先摘掉 Team，避免 kill 后的 task done 回调把 Team 目录写回来。
            self.teams.pop(team.sanitized_name, None)
            if self._active_name == team.sanitized_name:
                self._active_name = next(iter(self.teams), "")
            for member in list(team.members):
                if member.name == "lead":
                    continue
                await self._kill_member(team, member)
                self.registry.unregister(member.name)
                await self._cleanup_member(team, member)
            shutil.rmtree(team.config_dir, ignore_errors=True)

    async def _kill_member(self, team: Team, member: TeammateInfo) -> None:
        try:
            backend = self.backend_for(member)
            await backend.kill(member.pane_id, member.agent_id)
        except Exception as exc:  # noqa: BLE001 - best effort
            print(f"team: kill {member.name} 失败: {exc}", file=sys.stderr)

    async def _cleanup_member(self, team: Team, member: TeammateInfo) -> None:
        writer = self._member_writers.pop(member.agent_id, None)
        if writer is not None:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
        if member.session_dir:
            shutil.rmtree(member.session_dir, ignore_errors=True)
        if self.wt_mgr is None or not member.worktree_path:
            return
        try:
            from lcode.worktree import ExitOptions

            await self.wt_mgr.remove(
                f"team-{team.sanitized_name}/{member.name}",
                ExitOptions(discard_changes=True),
            )
        except Exception as exc:  # noqa: BLE001 - best effort
            print(f"team: cleanup {member.name} worktree 失败: {exc}", file=sys.stderr)

    def backend_for(self, member: TeammateInfo):
        from lcode.team.backend import new_backend

        return new_backend(
            member.backend_type,
            task_mgr=self.task_mgr,
            cwd=self.project_root,
        )

    # ── teammate spawning ────────────────────────────────────────────────

    async def spawn_teammate(self, req: Any) -> str:
        from lcode.team.spawn import spawn_teammate

        return await spawn_teammate(self, req)

    # ── notifications / mail ─────────────────────────────────────────────

    async def handle_task_done(self, agent_id: str) -> None:
        name = self.registry.name_of(agent_id)
        if not name:
            return
        for team in self.teams.values():
            member = team.member_by_name(name)
            if member is None or member.name == "lead":
                continue
            member.is_active = False
            try:
                await team.set_member_active(name, False)
            except Exception:  # noqa: BLE001
                pass
            await Box(team.mailbox_dir).write(
                team.lead_agent_id,
                Message(
                    from_=name,
                    text=f"[idle] {name} (reason: available)",
                    type=MessageType.TEXT,
                ),
            )
            return

    async def poll_lead_mailboxes(self) -> list[LeadMessage]:
        collected: list[LeadMessage] = []
        for team in self.list_():
            box = Box(team.mailbox_dir)
            indices, messages = await box.read_unread(team.lead_agent_id)
            if not messages:
                continue
            await box.mark_read(team.lead_agent_id, indices)
            for message in messages:
                collected.append(
                    LeadMessage(
                        team_name=team.sanitized_name,
                        from_=message.from_,
                        type=str(message.type),
                        content=message.text,
                        timestamp=message.timestamp,
                    )
                )
        return collected


__all__ = ["LeadMessage", "Manager", "TeamError"]
