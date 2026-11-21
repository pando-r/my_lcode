"""Hard-coded context-management constants (not configurable, F36)."""

# 是 compact（上下文压缩/管理）子系统的全局硬编码常量表
from __future__ import annotations

SINGLE_RESULT_LIMIT = 50000  # 单个工具结果超过此字节数 → 落盘
MESSAGE_AGGREGATE_LIMIT = 200000  # 一条消息内多个结果合计超限 → 从大到小逐个落盘
SUMMARY_RESERVE = 20000  # tokens reserved for summary output
AUTO_SAFETY_MARGIN = 13000  # tokens
MANUAL_SAFETY_MARGIN = 3000  # tokens
RECOVERY_FILE_LIMIT = 5
RECOVERY_TOKENS_PER_FILE = 5000
RECENT_KEEP_TOKENS = 10000
RECENT_KEEP_MESSAGES = 5
MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES = 3
PTL_RETRY_LIMIT = 3
PTL_DROP_PERCENTAGE = 0.2
ESTIMATE_CHARS_PER_TOKEN = 3.5
PREVIEW_HEAD_BYTES = 2048  # 落盘后回填的头部预览字节上限
PREVIEW_HEAD_LINES = 20  # 头部预览行数上限
