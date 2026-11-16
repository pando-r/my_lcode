"""Context management: preventive tool-result offloading and LLM summarization."""

# 门面收敛：跨轮次的状态管理、工具结果落盘、摘要后回填
from __future__ import annotations

from mewcode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    FileReadRecord,
    RecoveryState,
    SessionContext,
    new_session_context,
    open_session_context,
    parse_session_time,
)
from mewcode.compact.token import estimate_tokens, message_chars, usage_anchor

__all__ = [
    "CompactCircuitBreaker",
    "ContentReplacementState",
    "FileReadRecord",
    "RecoveryState",
    "SessionContext",
    "new_session_context",
    "open_session_context",
    "parse_session_time",
    "estimate_tokens",
    "message_chars",
    "usage_anchor",
]
