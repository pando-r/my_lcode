"""Lifecycle hook system: YAML rules, dispatch engine, and action executors."""

from lcode.hook.engine import DispatchResult, Engine
from lcode.hook.event import BLOCKING_EVENTS, Event, is_blocking, parse_event
from lcode.hook.executor import ExecutionResult, Executor
from lcode.hook.loader import HookConfigError, load
from lcode.hook.matcher import eval_condition, get_by_path
from lcode.hook.rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    HttpAction,
    Payload,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)

__all__ = [
    "BLOCKING_EVENTS",
    "Action",
    "ActionType",
    "AtomCondition",
    "CombineMode",
    "Condition",
    "DispatchResult",
    "Engine",
    "Event",
    "ExecutionResult",
    "Executor",
    "HookConfigError",
    "HttpAction",
    "Payload",
    "PromptAction",
    "Rule",
    "ShellAction",
    "SubagentAction",
    "eval_condition",
    "get_by_path",
    "is_blocking",
    "load",
    "parse_event",
]
