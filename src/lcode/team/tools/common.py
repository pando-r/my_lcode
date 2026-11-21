"""Shared helpers for team tools."""

from __future__ import annotations

import json
from typing import Any

from lcode.agent.team_hook import current_teammate_context
from lcode.agent.tool_context import current_tool_context
from lcode.team.manager import Manager
from lcode.team.types import Team


def parse_args(args: str | dict) -> dict[str, Any]:
    if isinstance(args, dict):
        return args
    try:
        value = json.loads(args or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数不是合法 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("参数必须是 JSON 对象")
    return value


def resolve_team(manager: Manager) -> Team | None:
    teammate = current_teammate_context()
    if teammate is not None:
        team = manager.get(teammate.team_name)
        if team is not None:
            return team
    ctx = current_tool_context()
    if ctx is not None and ctx.is_subagent:
        # 普通 SubAgent 不应借用 Lead 的 Team 上下文。
        return None
    return manager.active_team()


def sender_name() -> str:
    teammate = current_teammate_context()
    return teammate.member_name if teammate is not None else "lead"


def json_result(value: Any, *, error: bool = False):
    from lcode.tool import Result

    return Result(content=json.dumps(value, ensure_ascii=False), is_error=error)


__all__ = ["json_result", "parse_args", "resolve_team", "sender_name"]
