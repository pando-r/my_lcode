"""Hook rule data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mewcode.hook.event import Event
from mewcode.permission.matcher import Matcher

Payload = dict[str, Any]


class CombineMode(StrEnum):
    ALL_OF = "all_of"
    ANY_OF = "any_of"


class ActionType(StrEnum):
    SHELL = "shell"
    PROMPT = "prompt"
    HTTP = "http"
    SUBAGENT = "subagent"


@dataclass(frozen=True)
class AtomCondition:
    # field 是 payload 中的点路径；match 复用 permission.Matcher。
    field: str
    matcher: Matcher


@dataclass(frozen=True)
class Condition:
    mode: CombineMode
    atoms: list[AtomCondition]


@dataclass(frozen=True)
class ShellAction:
    command: str


@dataclass(frozen=True)
class PromptAction:
    text: str


@dataclass(frozen=True)
class HttpAction:
    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None


@dataclass(frozen=True)
class SubagentAction:
    agent_name: str
    prompt: str


@dataclass(frozen=True)
class Action:
    type: ActionType
    shell: ShellAction | None = None
    prompt: PromptAction | None = None
    http: HttpAction | None = None
    subagent: SubagentAction | None = None


@dataclass(frozen=True)
class Rule:
    name: str
    event: Event
    action: Action
    condition: Condition | None = None
    only_once: bool = False
    asyncio_mode: bool = False  # YAML 的 `async`（避免 Python 关键字）
    timeout_s: float = 30.0
    source: str = ""


__all__ = [
    "Action",
    "ActionType",
    "AtomCondition",
    "CombineMode",
    "Condition",
    "HttpAction",
    "Payload",
    "PromptAction",
    "Rule",
    "ShellAction",
    "SubagentAction",
]
