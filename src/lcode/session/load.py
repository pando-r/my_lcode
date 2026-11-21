"""Load a conversation back from JSONL (F21 / AC14-AC15)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from lcode.llm import ROLE_ASSISTANT, ROLE_TOOL, Message, ToolCall, ToolResult
from lcode.session.writer import JSONL_NAME

logger = logging.getLogger(__name__)

_VALID_ROLES = ("user", "assistant", "tool")


def load_session(session_dir: str) -> list[Message]:
    # 从最后一个 compact 标记之后加载；坏行跳过；截断孤立的工具调用。
    path = Path(session_dir) / JSONL_NAME
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    start = 0
    for index, line in enumerate(lines):
        data = _parse(line)
        if data is not None and data.get("type") == "compact":
            start = index + 1

    messages: list[Message] = []
    for line in lines[start:]:
        data = _parse(line)
        if data is None or data.get("type"):
            continue
        role = data.get("role")
        if role not in _VALID_ROLES:
            continue
        messages.append(_to_message(role, data))

    return truncate_orphaned_tool_calls(messages)


def truncate_orphaned_tool_calls(msgs: list[Message]) -> list[Message]:
    # 最后一条带 tool_calls 的 assistant 消息之后没有 tool 消息 → 截断到它之前。
    last_call_index = -1
    for index, msg in enumerate(msgs):
        if msg.role == ROLE_ASSISTANT and msg.tool_calls:
            last_call_index = index
    if last_call_index < 0:
        return msgs
    if any(msg.role == ROLE_TOOL for msg in msgs[last_call_index + 1 :]):
        return msgs
    return msgs[:last_call_index]


def last_entry_ts(session_dir: str) -> int | None:
    # 最后一条带 ts 的记录的 Unix 时间戳，供时间跨度提醒使用。
    path = Path(session_dir) / JSONL_NAME
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        data = _parse(line)
        if data is None:
            continue
        ts = data.get("ts")
        if isinstance(ts, int):
            return ts
    return None


# ── internals ────────────────────────────────────────────────────────────


def _parse(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _to_message(role: str, data: dict[str, Any]) -> Message:
    tool_calls: list[ToolCall] = []
    for call in data.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        try:
            tool_calls.append(
                ToolCall(
                    id=str(call.get("id", "")),
                    name=str(call.get("name", "")),
                    input=str(call.get("input", "")),
                )
            )
        except (TypeError, ValueError):
            logger.warning("跳过无法解析的 tool_call: %r", call)

    tool_results: list[ToolResult] = []
    for result in data.get("tool_results") or []:
        if not isinstance(result, dict):
            continue
        tool_results.append(
            ToolResult(
                tool_call_id=str(result.get("tool_call_id", "")),
                content=str(result.get("content", "")),
                is_error=bool(result.get("is_error", False)),
            )
        )

    content = data.get("content")
    return Message(
        role=role,  # type: ignore[arg-type]
        content=content if isinstance(content, str) else "",
        tool_calls=tool_calls,
        tool_results=tool_results,
    )
