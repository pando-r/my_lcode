"""Token estimation anchored on real usage + character increments (F13/F14/F22)."""

# token估计函数
from __future__ import annotations

import math

from lcode.compact.const import ESTIMATE_CHARS_PER_TOKEN
from lcode.llm import Message, Usage


def usage_anchor(u: Usage) -> int:
    """Merge a stream-end usage event into a single anchor value."""
    return u.input_tokens + u.output_tokens + u.cache_read + u.cache_write


def message_chars(msgs: list[Message]) -> int:
    """Total UTF-8 byte length of a message list (content + tool inputs + tool results)."""
    """计算消息列表的总字符数"""
    total = 0
    for m in msgs:
        total += len(m.content.encode("utf-8"))
        for c in m.tool_calls:
            total += len(c.input.encode("utf-8"))
        for r in m.tool_results:
            total += len(r.content.encode("utf-8"))
    return total


def estimate_tokens(anchor: int, all_msgs: list[Message], anchor_msg_len: int) -> int:
    """anchor + ceil(chars of messages after anchor_msg_len / 3.5)."""
    tail = all_msgs[anchor_msg_len:]  # 锚点后的消息列表
    return anchor + math.ceil(message_chars(tail) / ESTIMATE_CHARS_PER_TOKEN)
