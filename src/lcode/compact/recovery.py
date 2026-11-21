"""Three-section recovery attachment after summarization (F15-F18)."""

# 给有损压缩打补丁
from __future__ import annotations

import json

from lcode.compact.const import (
    ESTIMATE_CHARS_PER_TOKEN,
    RECOVERY_FILE_LIMIT,
    RECOVERY_TOKENS_PER_FILE,
)
from lcode.compact.state import FileReadRecord
from lcode.llm import ToolDefinition

BOUNDARY_NOTICE = """\
需要文件原文、错误原文、用户原话时，请使用文件读取工具重新读取对应路径，不要依据摘要内容做猜测。"""


def render_file_block(rec: FileReadRecord) -> str:
    char_limit = int(RECOVERY_TOKENS_PER_FILE * ESTIMATE_CHARS_PER_TOKEN)
    fragment = rec.content
    truncated = ""
    if len(fragment) > char_limit:
        fragment = fragment[:char_limit]
        truncated = "\n(content truncated)"
    return f"### {rec.path}\n[read at] {rec.timestamp.isoformat()}\n{fragment}{truncated}\n"


def render_tools_block(defs: list[ToolDefinition]) -> str:
    lines: list[str] = []
    for d in defs:
        lines.append(f"- {d.name}: {d.description}")
        schema = json.dumps(d.input_schema, separators=(",", ":"), ensure_ascii=False)
        lines.append(f"  {schema}")
    return "\n".join(lines)


def build_recovery_attachment(
    snapshot: list[FileReadRecord],
    tool_defs: list[ToolDefinition],
) -> str:
    parts: list[str] = []
    parts.append("## 最近读过的文件")
    top = snapshot[:RECOVERY_FILE_LIMIT]
    if top:
        parts.append("\n".join(render_file_block(rec) for rec in top))
    else:
        parts.append("(无)")
    parts.append("## 当前可用工具")
    parts.append(render_tools_block(tool_defs))
    parts.append("## 边界提示")
    parts.append(BOUNDARY_NOTICE)
    return "\n\n".join(parts)
