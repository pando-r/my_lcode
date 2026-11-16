"""Spawn a teammate into a Team across backends."""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING

from mewcode.agent.fork import build_forked_messages
from mewcode.agent.team_hook import IncomingMessage, TeammateContext
from mewcode.conversation import Conversation
from mewcode.permission import Mode
from mewcode.session import Writer
from mewcode.team.backend import SpawnRequest, new_backend
from mewcode.team.mailbox import Box, Message, MessageType
from mewcode.team.types import (
    BackendType,
    MemberExistsError,
    Team,
    TeammateInfo,
    TeamNotFoundError,
)
from mewcode.tool.filter import FilterParams, apply_agent_tool_filter

if TYPE_CHECKING:
    from mewcode.team.manager import Manager

TEAM_SYSTEM_PROMPT_SUFFIX = """
IMPORTANT: You are running as an agent in a team.
Just writing a response in text is not visible to others
on your team - you MUST use the SendMessage tool.
The user interacts primarily with the team lead.
Your work is coordinated through the task system
and teammate messaging.
"""


def build_team_context_reminder(
    team: Team, member_name: str, agent_id: str, worktree_path: str
) -> str:
    members = ", ".join(f"{member.name}({member.agent_type or 'lead'})" for member in team.members)
    return (
        "<team-context>\n"
        f"team: {team.sanitized_name}\n"
        f"你的成员名: {member_name}\n"
        f"你的 agent_id: {agent_id}\n"
        f"worktree 目录: {worktree_path}\n"
        f"当前团队成员: {members}\n"
        "</team-context>"
    )


async def spawn_teammate(manager: Manager, req) -> str:
    team = manager.get(req.team_name)
    if team is None:
        raise TeamNotFoundError(f"未知 Team: {req.team_name}")
    parent = req.parent_agent
    if parent is None:
        raise ValueError("Team spawn 缺少父 Agent 上下文")

    catalog = manager.catalog
    if catalog is None:
        raise ValueError("SubAgent catalog 未配置")

    definition = None
    forked = False
    if req.agent_type:
        definition = catalog.resolve(req.agent_type)
        if definition is None:
            raise ValueError(f"未知 subagent_type: {req.agent_type}")
    elif manager.fork_teammate_enabled:
        definition = catalog.fork_definition()
        forked = True
    else:
        definition = catalog.resolve("general-purpose")
        if definition is None:
            raise ValueError("缺少内置 general-purpose 角色")

    member_name = req.member_name or f"agent-{secrets.token_hex(4)[:7]}"
    if team.member_by_name(member_name) is not None:
        raise MemberExistsError(f"成员已存在: {member_name}")
    if manager.wt_mgr is None:
        raise ValueError("Worktree Manager 未启用，无法创建队员工作目录")

    worktree = await manager.wt_mgr.create(
        f"team-{team.sanitized_name}/{member_name}", "HEAD", manual=False
    )
    from mewcode.compact import new_session_context

    session_ctx = new_session_context(manager.project_root)
    agent_id = f"agent-{secrets.token_hex(4)[:7]}"

    all_names = [item.name for item in parent.registry.definitions()]
    allowed = apply_agent_tool_filter(
        FilterParams(
            all=all_names,
            source=int(definition.source),
            background=False,
            allowed=definition.tools,
            disallowed=definition.disallowed_tools,
            teammate=True,
            keep_agent=True,
        )
    )

    box = Box(team.mailbox_dir)
    teammate_ctx = TeammateContext(
        team_name=team.sanitized_name,
        member_name=member_name,
        agent_id=agent_id,
        backend_type=str(team.backend),
        read_unread=_make_read_unread(box, agent_id),
        mark_read=_make_mark_read(box, agent_id),
    )
    context_reminder = build_team_context_reminder(team, member_name, agent_id, worktree.path)

    if team.backend is BackendType.IN_PROCESS:
        if manager.task_mgr is None:
            raise ValueError("task.Manager 未配置，无法启动 in-process 队员")
        from mewcode.agent import Agent
        from mewcode.agent.runtime import SessionRuntime

        writer = Writer(session_ctx.session_dir)
        manager._member_writers[agent_id] = writer
        runtime = SessionRuntime(
            instruction_text=parent.runtime.instruction_text,
            memory_text=parent.runtime.memory_text,
            session=parent.runtime.session,
            context_window=200_000,
            skill_catalog=parent.runtime.skill_catalog,
        )
        if forked and req.parent_conv is not None:
            messages = build_forked_messages(req.parent_conv.messages(), req.prompt)
            conv = Conversation.from_messages(
                messages, on_append=writer.on_append, on_replace=writer.on_replace
            )
            task_text = ""
        else:
            conv = Conversation(on_append=writer.on_append, on_replace=writer.on_replace)
            task_text = req.prompt
        runtime.append_reminders([context_reminder])
        sub_agent = Agent(
            provider=parent.provider,
            registry=parent.registry,
            version=parent.version,
            engine=parent.engine,
            runtime=runtime,
            hook_engine=parent.hook_engine,
            system_prompt=definition.system_prompt or None,
            max_turns=definition.max_turns,
            permission_mode=(
                Mode.PLAN if definition.plan_mode_required else definition.permission_mode
            ),
            dont_ask=True,
            allowed_tools=allowed,
            is_subagent=True,
            teammate_context=teammate_ctx,
        )
        sub_agent.append_system_prompt(TEAM_SYSTEM_PROMPT_SUFFIX)
        request = SpawnRequest(
            team_name=team.sanitized_name,
            member_name=member_name,
            agent_id=agent_id,
            worktree_path=worktree.path,
            session_dir=session_ctx.session_dir,
            agent_type=definition.name if not forked else "",
            model=req.model or ("" if definition.model == "inherit" else definition.model),
            initial_prompt=task_text,
            plan_mode_required=definition.plan_mode_required,
            sub_agent=sub_agent,
            conv=conv,
            task_mgr=manager.task_mgr,
        )
    else:
        # Pane 后端：初始任务先写邮箱，子进程启动后自然读到。
        await box.write(
            agent_id,
            Message(from_="lead", text=req.prompt, type=MessageType.TEXT),
        )
        request = SpawnRequest(
            team_name=team.sanitized_name,
            member_name=member_name,
            agent_id=agent_id,
            worktree_path=worktree.path,
            session_dir=session_ctx.session_dir,
            agent_type=definition.name if not forked else "",
            model=req.model or ("" if definition.model == "inherit" else definition.model),
            initial_prompt=req.prompt,
            plan_mode_required=definition.plan_mode_required,
        )

    # 先落盘成员再 launch：Pane 子进程启动时要能读到自己的成员条目；
    # in-process 的 task done 回调也可能在 spawn 返回前触发。
    member = TeammateInfo(
        name=member_name,
        agent_id=agent_id,
        agent_type=definition.name if not forked else "",
        model=request.model,
        worktree_path=worktree.path,
        branch=worktree.branch,
        backend_type=team.backend,
        pane_id="",
        is_active=None,
        plan_mode_required=definition.plan_mode_required,
        session_dir=session_ctx.session_dir,
    )
    manager.registry.register(member_name, agent_id)
    await team.add_member(member)

    backend = new_backend(team.backend, task_mgr=manager.task_mgr, cwd=manager.project_root)
    try:
        pane_id, actual_id = await backend.spawn(request)
    except Exception:
        manager.registry.unregister(member_name)
        try:
            await team.remove_member(member_name)
        except Exception:  # noqa: BLE001
            pass
        import shutil

        shutil.rmtree(session_ctx.session_dir, ignore_errors=True)
        try:
            from mewcode.worktree import ExitOptions

            await manager.wt_mgr.remove(
                f"team-{team.sanitized_name}/{member_name}",
                ExitOptions(discard_changes=True),
            )
        except Exception:  # noqa: BLE001
            pass
        raise

    if actual_id != agent_id:
        manager.registry.register(member_name, actual_id)
    member.agent_id = actual_id
    member.pane_id = pane_id
    try:
        await team.update_member(member, preserve_active=True)
    except Exception:  # noqa: BLE001 - in-memory already correct
        pass
    manager.set_active(team.sanitized_name)
    return json.dumps(
        {
            "member_name": member_name,
            "agent_id": actual_id,
            "worktree": worktree.path,
            "backend": str(team.backend),
            "pane_id": pane_id,
        },
        ensure_ascii=False,
    )


def _make_read_unread(box: Box, agent_id: str):
    async def read_unread():
        indices, messages = await box.read_unread(agent_id)
        return indices, [
            IncomingMessage(
                from_=message.from_,
                text=message.text,
                type=str(message.type),
                request_id=message.request_id,
                approve=message.approve,
                timestamp=message.timestamp,
            )
            for message in messages
        ]

    return read_unread


def _make_mark_read(box: Box, agent_id: str):
    async def mark_read(indices):
        await box.mark_read(agent_id, indices)

    return mark_read


__all__ = [
    "TEAM_SYSTEM_PROMPT_SUFFIX",
    "build_team_context_reminder",
    "spawn_teammate",
]
