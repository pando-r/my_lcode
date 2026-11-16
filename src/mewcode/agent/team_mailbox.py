"""Inject teammate mailbox messages into an Agent iteration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mewcode.permission import Mode

if TYPE_CHECKING:
    from mewcode.agent import Agent
    from mewcode.agent.team_hook import IncomingMessage


def build_incoming_messages(messages: list[IncomingMessage]) -> str:
    lines = ["<incoming-messages>", f"收到 {len(messages)} 条新消息:"]
    for index, message in enumerate(messages, 1):
        header = f"[{index}] 来自 {message.from_} (type={message.type}, ts={message.timestamp}):"
        lines.append(header)
        lines.append("    " + message.text.replace("\n", "\n    "))
    lines.append("</incoming-messages>")
    return "\n".join(lines)


async def ingest_teammate_mailbox(agent: Agent, mode: Mode) -> None:
    ctx = agent.teammate_context
    if ctx is None or ctx.read_unread is None or ctx.mark_read is None:
        return
    indices, messages = await ctx.read_unread()
    if not messages:
        return

    for message in messages:
        if message.type == "plan_approval_response":
            if message.approve:
                agent.set_permission_mode(Mode.DEFAULT)
            else:
                agent.set_permission_mode(Mode.PLAN)
        elif message.type == "shutdown_request":
            # 由 LLM 决定是否回复 shutdown_response；这里只呈现消息。
            pass

    reminder = build_incoming_messages(messages)
    agent.runtime.append_reminders([reminder])
    await ctx.mark_read(indices)


__all__ = ["build_incoming_messages", "ingest_teammate_mailbox"]
