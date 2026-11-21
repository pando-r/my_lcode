"""Autonomous teammate loop for pane backends (``--team-member``)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from lcode.agent import Agent
from lcode.agent.runtime import SessionRuntime
from lcode.agent.team_hook import IncomingMessage, TeammateContext
from lcode.conversation import Conversation
from lcode.llm import new_provider
from lcode.permission import Mode
from lcode.session import Writer, load_session
from lcode.team.mailbox import Box, Message, MessageType
from lcode.team.spawn import TEAM_SYSTEM_PROMPT_SUFFIX
from lcode.tool.filter import FilterParams, apply_agent_tool_filter


async def run_team_member(
    args: Any,
    *,
    team_mgr: Any,
    catalog: Any,
    registry: Any,
    engine: Any,
    hook_engine: Any,
    cfg: Any,
) -> None:
    team = team_mgr.get(args.team)
    if team is None:
        print(f"[team-member] 未知 Team: {args.team}", file=sys.stderr)
        return
    member = team.member_by_name(args.member) or team.member_by_agent_id(args.agent_id)
    if member is None:
        # 跨进程竞态：子进程可能先于 Lead 的 add_member 启动，用 CLI 参数自描述。
        from lcode.team.types import TeammateInfo

        member = TeammateInfo(
            name=args.member,
            agent_id=args.agent_id,
            agent_type=args.agent_type,
            worktree_path=args.worktree,
            backend_type=team.backend,
            session_dir=args.session_dir,
        )

    definition = (catalog.resolve(args.agent_type) if args.agent_type else None) or catalog.resolve(
        "general-purpose"
    )
    if definition is None:
        print("[team-member] 缺少 Agent 定义", file=sys.stderr)
        return

    provider_cfg = cfg.providers[0]
    provider = new_provider(provider_cfg)
    model = args.model or ("" if definition.model == "inherit" else definition.model)
    if model and hasattr(provider, "with_model"):
        try:
            provider = provider.with_model(model)
        except Exception:
            pass

    session_dir = args.session_dir
    writer = Writer(session_dir)
    try:
        messages = load_session(session_dir)
    except Exception:
        messages = []
    conv = Conversation.from_messages(
        messages, on_append=writer.on_append, on_replace=writer.on_replace
    )

    all_names = [item.name for item in registry.definitions()]
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
        member_name=member.name,
        agent_id=args.agent_id,
        backend_type=str(team.backend),
        read_unread=_read_unread(box, args.agent_id),
        mark_read=_mark_read(box, args.agent_id),
    )
    runtime = SessionRuntime(context_window=200_000)
    runtime.append_reminders(
        [
            "<team-context>\n"
            f"team: {team.sanitized_name}\n"
            f"你的成员名: {member.name}\n"
            f"你的 agent_id: {args.agent_id}\n"
            f"worktree 目录: {Path.cwd()}\n"
            "</team-context>"
        ]
    )
    agent = Agent(
        provider=provider,
        registry=registry,
        version="dev",
        engine=engine,
        runtime=runtime,
        hook_engine=hook_engine,
        system_prompt=definition.system_prompt or None,
        max_turns=definition.max_turns,
        permission_mode=(
            Mode.PLAN
            if (args.plan_mode or definition.plan_mode_required)
            else definition.permission_mode
        ),
        dont_ask=True,
        allowed_tools=allowed,
        is_subagent=True,
        teammate_context=teammate_ctx,
    )
    agent.append_system_prompt(TEAM_SYSTEM_PROMPT_SUFFIX)

    print(
        f"[team-member] {member.name} · team={team.sanitized_name} · "
        f"agent={args.agent_id} · cwd={Path.cwd()}",
        flush=True,
    )

    while True:
        if not Path(team.mailbox_dir).is_dir():
            print("[team-member] Team 已删除，退出", flush=True)
            break
        try:
            indices, raw = await box.read_unread(args.agent_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[team-member] 读取邮箱失败: {exc}", file=sys.stderr)
            await asyncio.sleep(1)
            continue
        messages_in = [
            IncomingMessage(
                from_=item.from_,
                text=item.text,
                type=str(item.type),
                request_id=item.request_id,
                approve=item.approve,
                timestamp=item.timestamp,
            )
            for item in raw
        ]
        await box.mark_read(args.agent_id, indices)
        if not messages_in:
            await asyncio.sleep(1)
            continue

        task_parts: list[str] = []
        for message in messages_in:
            if message.type == str(MessageType.PLAN_APPROVAL_RESPONSE):
                if message.approve:
                    agent.set_permission_mode(Mode.DEFAULT)
                    task_parts.append("Lead 已批准计划，权限模式已切到 default，请执行计划。")
                else:
                    agent.set_permission_mode(Mode.PLAN)
                    task_parts.append(f"Lead 驳回了计划，反馈: {message.text}\n请调整后重新提交。")
            elif message.type == str(MessageType.SHUTDOWN_REQUEST):
                task_parts.append(
                    "Lead 请求收工: "
                    f"{message.text}\n如已完成，回复 shutdown_response(approve=True)。"
                )
            else:
                task_parts.append(message.text)

        events: asyncio.Queue = asyncio.Queue()
        printer = asyncio.create_task(_print_events(events))
        try:
            await agent.run_to_completion(conv, "\n\n".join(task_parts), events)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[team-member] 执行失败: {exc}", file=sys.stderr)
        finally:
            printer.cancel()

        try:
            await box.write(
                team.lead_agent_id,
                Message(
                    from_=member.name,
                    text=f"[idle] {member.name} (reason: available)",
                    type=MessageType.TEXT,
                ),
            )
            await team.set_member_active(member.name, False)
        except Exception:  # noqa: BLE001
            pass


def _read_unread(box: Box, agent_id: str):
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


def _mark_read(box: Box, agent_id: str):
    async def mark_read(indices):
        await box.mark_read(agent_id, indices)

    return mark_read


async def _print_events(events: asyncio.Queue) -> None:
    while True:
        event = await events.get()
        if event.text:
            print(event.text, end="", flush=True)
        if event.tool is not None and event.tool.phase.value == "start":
            print(f"\n● {event.tool.name}({event.tool.args})", flush=True)
        if event.done:
            print("\n---", flush=True)


__all__ = ["run_team_member"]
