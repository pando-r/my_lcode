"""Lifecycle hook system: YAML rules, dispatch engine, and action executors."""

from mewcode.hook.engine import DispatchResult, Engine
from mewcode.hook.event import BLOCKING_EVENTS, Event, is_blocking, parse_event
from mewcode.hook.executor import ExecutionResult, Executor
from mewcode.hook.loader import HookConfigError, load
from mewcode.hook.matcher import eval_condition, get_by_path
from mewcode.hook.rule import (
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
