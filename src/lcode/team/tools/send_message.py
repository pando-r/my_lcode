"""SendMessage: Team mailbox addressing with background-task fallback."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from lcode.team.mailbox import Box, Message, MessageType
from lcode.team.manager import Manager
from lcode.team.tools.common import json_result, parse_args, resolve_team, sender_name
from lcode.team.types import BackendType, Team, TeammateInfo
from lcode.tool import Result


class TeamAwareSendMessageTool:
    read_only = False
    is_system = True

    def __init__(self, manager: Manager, fallback) -> None:
        self._manager = manager
        self._fallback = fallback

    def name(self) -> str:
        return "SendMessage"

    def description(self) -> str:
        return "Team 上下文：给队员/Lead/广播发消息；否则给后台子 Agent 续派任务。"

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "队员名 / agent_id / * / lead"},
                "content": {"type": "string"},
                "type": {
                    "type": "string",
                    "enum": [
                        "text",
                        "shutdown_request",
                        "shutdown_response",
                        "plan_approval_request",
                        "plan_approval_response",
                    ],
                },
                "request_id": {"type": "string"},
                "approve": {"type": "boolean"},
                "name": {"type": "string", "description": "后台任务名（非 Team 模式）"},
                "message": {"type": "string", "description": "后台任务消息（非 Team 模式）"},
            },
        }

    async def execute(self, args: str) -> Result:
        team = resolve_team(self._manager)
        if team is None:
            try:
                raw = parse_args(args)
            except ValueError:
                return await self._fallback.execute(args)
            if "to" in raw or "content" in raw:
                translated = {
                    "name": raw.get("to") or raw.get("name", ""),
                    "message": raw.get("content") or raw.get("message", ""),
                }
                return await self._fallback.execute(json.dumps(translated))
            return await self._fallback.execute(args)
        try:
            data = parse_args(args)
        except ValueError as exc:
            return Result(content=str(exc), is_error=True)

        target = str(data.get("to") or data.get("name") or "").strip()
        content = str(data.get("content") or data.get("message") or "")
        if not target or not content:
            return Result(content="to/content 不能为空", is_error=True)
        try:
            message_type = MessageType(str(data.get("type", "text")))
        except ValueError:
            return Result(content="未知消息类型", is_error=True)

        sender = sender_name()
        request_id = str(data.get("request_id", ""))
        approve = data.get("approve")
        if message_type is MessageType.PLAN_APPROVAL_RESPONSE and sender != "lead":
            return Result(content="只有 Lead 可以发送 plan_approval_response", is_error=True)

        targets = _resolve_targets(self._manager, team, target, sender)
        if not targets:
            return Result(content=f"找不到消息目标: {target}", is_error=True)
        if message_type is MessageType.SHUTDOWN_RESPONSE and any(
            member.name != "lead" for member in targets
        ):
            return Result(content="shutdown_response 只能发给 Lead", is_error=True)

        box = Box(team.mailbox_dir)
        for member in targets:
            await box.write(
                member.agent_id,
                Message(
                    from_=sender,
                    text=content,
                    type=message_type,
                    request_id=request_id,
                    approve=approve if isinstance(approve, bool) else None,
                ),
            )
            await self._wake_or_resume(team, member, message_type, content, approve)

        return json_result(
            {
                "delivered_to": [member.agent_id for member in targets],
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    async def _wake_or_resume(
        self,
        team: Team,
        member: TeammateInfo,
        message_type: MessageType,
        content: str,
        approve: object = None,
    ) -> None:
        if member.name == "lead":
            return
        if member.backend_type is not BackendType.IN_PROCESS:
            try:
                await self._manager.backend_for(member).wake(member.pane_id, member.agent_id)
            except Exception:
                pass
            return
        if message_type not in (
            MessageType.TEXT,
            MessageType.PLAN_APPROVAL_RESPONSE,
        ):
            return
        task = (
            self._manager.task_mgr.get(member.agent_id)
            if self._manager.task_mgr is not None
            else None
        )
        if task is None or task.status.name == "RUNNING":
            return
        if message_type is MessageType.PLAN_APPROVAL_RESPONSE and task.sub_agent is not None:
            from lcode.permission import Mode

            task.sub_agent.set_permission_mode(Mode.DEFAULT if approve is not False else Mode.PLAN)
        try:
            await team.set_member_active(member.name, True)
            await self._manager.task_mgr.send_message(member.name, content, allow_stopped=True)
        except Exception:
            # 失败/取消的任务无法直接续派；保留邮箱消息，由 Lead 决定是否重派。
            pass


def _resolve_targets(manager: Manager, team: Team, target: str, sender: str) -> list[TeammateInfo]:
    if target == "*":
        return [member for member in team.members if member.name != sender]
    if target == "lead":
        lead = team.member_by_name("lead")
        return [lead] if lead is not None else []
    member = team.member_by_name(target)
    if member is not None:
        return [member]
    resolved = manager.registry.resolve(target)
    if resolved:
        by_id = team.member_by_agent_id(resolved)
        if by_id is not None:
            return [by_id]
    return []


SendMessageTool = TeamAwareSendMessageTool

__all__ = ["SendMessageTool", "TeamAwareSendMessageTool"]
