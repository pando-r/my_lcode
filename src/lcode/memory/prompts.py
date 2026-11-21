"""Prompts for the asynchronous memory-update request (F37-F39)."""

from __future__ import annotations

from lcode.llm import Message

MEMORY_UPDATE_SYSTEM_PROMPT = """\
You maintain a long-term memory store for a coding assistant.

You receive the current memory indexes and the most recent conversation turn.
Decide what, if anything, is worth remembering across future sessions.

Rules:
- Only store durable facts: user preferences, corrections/feedback, project knowledge,
  or reference material. Do not store transient chatter or task progress.
- `user_preference` and `correction_feedback` belong at level "user" (cross-project).
- `project_knowledge` and `reference_material` belong at level "project".
- Check the existing index first. If a similar note exists, prefer "update" over "create".
  If a note is obsolete or contradicted, use "delete".
- Filenames look like `<type>_<short_slug>.md` (lowercase, underscore-separated).
- Keep each note short and self-contained.

Return ONLY a JSON array. Each element is one of:
[
  {"action":"create","level":"project","type":"project_knowledge",
   "title":"...","slug":"...","content":"..."},
  {"action":"update","level":"user","filename":"user_preference_x.md",
   "title":"...","content":"..."},
  {"action":"delete","level":"project","filename":"project_knowledge_old.md"}
]

Return [] when nothing should change. No prose, no code fences.
"""


def build_update_prompt(
    project_index: str,
    user_index: str,
    recent_msgs: list[Message],
) -> str:
    project_index = project_index.strip() or "（空）"
    user_index = user_index.strip() or "（空）"
    conversation = _serialize(recent_msgs)
    return (
        "[project index]\n"
        f"{project_index}\n\n"
        "[user index]\n"
        f"{user_index}\n\n"
        "[recent turn]\n"
        f"{conversation}\n"
    )


# 记忆更新提示词中的一个转换器：把 Message 列表压平成纯文本对话记录，
# 供“记忆更新”这个独立的 LLM 请求阅读。
def _serialize(msgs: list[Message]) -> str:
    lines: list[str] = []
    for msg in msgs:
        if msg.role == "tool":
            for result in msg.tool_results:
                lines.append(f"[tool result] {result.content}")
            continue
        if msg.tool_calls:
            for call in msg.tool_calls:
                lines.append(f"[tool call {call.name}] {call.input}")
        if msg.content:
            lines.append(f"{msg.role}: {msg.content}")
    return "\n".join(lines)
