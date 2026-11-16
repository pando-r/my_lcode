"""Permission system: five-layer defense before tool execution."""

from __future__ import annotations

from enum import IntEnum


class Mode(IntEnum):
    DEFAULT = 0
    ACCEPT_EDITS = 1
    PLAN = 2
    BYPASS = 3

    def __str__(self) -> str:
        return ["default", "acceptEdits", "plan", "bypassPermissions"][self.value]


class Decision(IntEnum):
    ALLOW = 0
    DENY = 1
    ASK = 2


class Category(IntEnum):
    READ = 0
    WRITE = 1
    EXEC = 2


class Outcome(IntEnum):
    DENY_ONCE = 0
    ALLOW_ONCE = 1
    ALLOW_FOREVER = 2


class ApprovalError(Exception):
    # Raised when the human-in-the-loop approval channel is misused.
    pass


def parse_mode(s: str) -> tuple[Mode, bool]:
    # Case-insensitive parse of the four mode names.
    if not isinstance(s, str):
        return Mode.DEFAULT, False
    lowered = s.strip().lower()
    table = {
        "default": Mode.DEFAULT,
        "acceptedits": Mode.ACCEPT_EDITS,
        "plan": Mode.PLAN,
        "bypasspermissions": Mode.BYPASS,
    }
    mode = table.get(lowered)
    if mode is None:
        return Mode.DEFAULT, False
    return mode, True


from mewcode.permission.engine import Engine, new_engine  # noqa: E402
from mewcode.permission.persist import persist_local_allow  # noqa: E402

__all__ = [
    "Mode",
    "Decision",
    "Category",
    "Outcome",
    "ApprovalError",
    "parse_mode",
    "Engine",
    "new_engine",
    "persist_local_allow",
]
