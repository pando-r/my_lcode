"""Fork-path conversation cloning and boilerplate detection."""

from __future__ import annotations

import copy

from mewcode.llm import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER, Message, ToolResult

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"

FORK_BOILERPLATE = """<fork_boilerplate>
你是一个 Fork 出来的工作进程。你不是主 Agent。
规则（不可协商）：
1. 不能再 Fork（调用 Agent 工具会被拦截）。
2. 不要对话、不要提问、不要请求确认。
3. 直接使用工具：读文件、搜索代码、做修改。
4. 严格限制在你被分配的任务范围内。
5. 最终报告以 "Scope:" 开头，500 字以内。
</fork_boilerplate>

"""


def build_forked_messages(parent_msgs: list[Message], task: str) -> list[Message]:
    # 深拷贝父对话 → 补齐悬空 tool_use → 追加 Boilerplate + task。
    messages = copy.deepcopy(list(parent_msgs))

    answered: set[str] = set()
    for message in messages:
        if message.role == ROLE_TOOL:
            answered.update(result.tool_call_id for result in message.tool_results)

    last_assistant = next(
        (message for message in reversed(messages) if message.role == ROLE_ASSISTANT), None
    )
    if last_assistant is not None:
        missing = [call.id for call in last_assistant.tool_calls if call.id not in answered]
        if missing:
            messages.append(
                Message(
                    role=ROLE_TOOL,
                    tool_results=[
                        ToolResult(
                            tool_call_id=call_id,
                            content="[forked, skipped]",
                            is_error=True,
                        )
                        for call_id in missing
                    ],
                )
            )

    messages.append(Message(role=ROLE_USER, content=FORK_BOILERPLATE + task))
    return messages


def is_fork_context(msgs: list[Message]) -> bool:
    # QuerySource 失效时的兜底：历史中出现 Boilerplate 标记即视为 Fork 子 Agent。
    for message in msgs:
        if FORK_BOILERPLATE_TAG in (message.content or ""):
            return True
        for result in message.tool_results:
            if FORK_BOILERPLATE_TAG in (result.content or ""):
                return True
    return False


__all__ = [
    "FORK_BOILERPLATE",
    "FORK_BOILERPLATE_TAG",
    "build_forked_messages",
    "is_fork_context",
]
