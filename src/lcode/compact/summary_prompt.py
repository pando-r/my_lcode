"""Summary prompt template and parsing (F9/F10)."""

from __future__ import annotations

import logging
import re

from lcode.llm import ROLE_USER, Message

SUMMARY_INSTRUCTION = """\
You are summarizing a coding agent conversation. Output in two phases.

<analysis>
（在这里写分析草稿，会被丢弃）
</analysis>

<summary>
## 1 主要请求和意图
## 2 关键技术概念
## 3 文件和代码段
## 4 错误和修复
## 5 问题解决过程
## 6 所有用户消息原文
## 7 待办任务
## 8 当前工作(最详细)
## 9 可能的下一步
</summary>

不要调用任何工具，输出纯文本。"""


def build_summary_prompt(msgs: list[Message]) -> list[Message]:
    serialized = serialize_conversation(msgs)
    content = SUMMARY_INSTRUCTION + "\n\n[conversation]\n" + serialized
    return [Message(role=ROLE_USER, content=content)]


def serialize_conversation(msgs: list[Message]) -> str:
    """将消息列表序列化为文本格式"""
    lines: list[str] = []
    for m in msgs:
        if m.role in ("user", "assistant"):
            if m.role == "assistant" and m.tool_calls:
                for c in m.tool_calls:
                    lines.append(f"[call {c.name} id={c.id} args={c.input}]")
            if m.content:
                lines.append(f"{m.role}: {m.content}")
        elif m.role == "tool":
            for r in m.tool_results:
                lines.append(f"[result id={r.tool_call_id} is_error={r.is_error}] {r.content}")
    return "\n".join(lines)


def extract_summary(raw: str) -> str:
    """从原始文本中提取摘要"""
    matches = re.findall(r"<summary>(.*?)</summary>", raw, re.DOTALL)
    if not matches:
        logging.getLogger(__name__).warning("summary tags not found")
        return raw
    return matches[-1].strip()
