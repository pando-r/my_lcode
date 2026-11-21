"""system-reminder construction and plan-mode reminders."""

from __future__ import annotations

EXECUTE_DIRECTIVE = "请按上面的计划开始执行。"

_PLAN_REMINDER_FULL = (
    "You are currently in PLAN MODE. You may use ONLY the read-only tools (read_file, glob, "
    "grep) to investigate the codebase. You must NOT write files, edit files, or run shell "
    "commands. Produce a clear, step-by-step plan for the task, then stop and wait for the "
    "user to approve it before doing any work."
)

_PLAN_REMINDER_CONCISE = "PLAN MODE: read-only tools only; produce a plan and wait for approval."


def system_reminder(body: str) -> str:
    # Wrap supplementary instruction in a system-reminder tag.
    return f"<system-reminder>\n{body}\n</system-reminder>"


def plan_reminder(full: bool) -> str:
    # Return a tagged plan-mode reminder: full or concise.
    return system_reminder(_PLAN_REMINDER_FULL if full else _PLAN_REMINDER_CONCISE)
