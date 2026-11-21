"""In-memory single-session conversation history."""

# 会话历史记录
from __future__ import annotations

import copy
import logging
from collections.abc import Callable

from lcode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ToolCall,
    ToolResult,
)

# 日志记录器
logger = logging.getLogger(__name__)
# 回调函数类型，消息接收器
_OnAppend = Callable[[Message], None]
# 回调函数类型，消息列表接收器
_OnReplace = Callable[[list[Message]], None]


# 这次对话到目前为止说过的每一句话的记录
class Conversation:
    def __init__(
        self,
        on_append: _OnAppend | None = None,
        on_replace: _OnReplace | None = None,
    ) -> None:
        self._messages: list[Message] = []
        self._on_append = on_append
        self._on_replace = on_replace

    @classmethod
    def from_messages(
        cls,
        msgs: list[Message],
        on_append: _OnAppend | None = None,
        on_replace: _OnReplace | None = None,
    ) -> Conversation:
        # 从已有消息列表创建会话（恢复场景），不触发回调。
        conv = cls(on_append=on_append, on_replace=on_replace)  # 创建一个新的会话实例
        conv._messages = copy.deepcopy(list(msgs))
        return conv

    def add_user(self, text: str) -> None:
        self._append(Message(role=ROLE_USER, content=text))

    def add_assistant(self, text: str) -> None:
        self._append(Message(role=ROLE_ASSISTANT, content=text))

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        # Append an assistant turn that requested tool calls.
        self._append(Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls)))

    def add_tool_results(self, results: list[ToolResult]) -> None:
        # Append a tool-results turn.
        self._append(Message(role=ROLE_TOOL, tool_results=list(results)))

    def last_role(self) -> str:
        # Return the role of the last message, or "" for an empty history.
        return self._messages[-1].role if self._messages else ""

    def messages(self) -> list[Message]:
        # Return a copy of the full history.
        return list(self._messages)

    def replace_history(self, msgs: list[Message]) -> None:
        # Replace the in-memory history with a deep copy of msgs.
        self._messages = copy.deepcopy(msgs)
        self._fire_replace()

    # ch09 spec 使用 replace_messages 这个名字；保留 replace_history 作为别名。
    replace_messages = replace_history

    # ── internals ────────────────────────────────────────────────────────

    def _append(self, msg: Message) -> None:
        self._messages.append(msg)
        if self._on_append is None:
            return
        try:
            self._on_append(msg)
        except Exception:  # noqa: BLE001 - persistence must not break the conversation
            logger.exception("on_append callback failed")

    def _fire_replace(self) -> None:
        if self._on_replace is None:
            return
        try:
            self._on_replace(list(self._messages))
        except Exception:  # noqa: BLE001 - persistence must not break the conversation
            logger.exception("on_replace callback failed")
