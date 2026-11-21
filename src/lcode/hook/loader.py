"""Load hook rules from project/user YAML files."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from lcode.hook.engine import Engine
from lcode.hook.event import is_blocking, parse_event
from lcode.hook.rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    HttpAction,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)
from lcode.permission.matcher import (
    ExactMatcher,
    GlobMatcher,
    Matcher,
    NotMatcher,
    RegexMatcher,
)

PROJECT_HOOKS_FILE = ".lcode/hooks.yaml"
USER_HOOKS_FILE = ".lcode/hooks.yaml"

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*$")
_MATCH_TYPES = ("exact", "glob", "regex", "not")


class HookConfigError(ValueError):
    pass


def load(project_root: str | Path) -> Engine:
    # 项目级在前、用户级在后；同名后到者跳过。所有错误只报 stderr。
    root = Path(project_root)
    candidates = [root / PROJECT_HOOKS_FILE, Path.home() / USER_HOOKS_FILE]

    rules: list[Rule] = []
    sources: list[str] = []
    names: set[str] = set()
    seen_paths: set[Path] = set()

    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        if not path.is_file():
            continue
        if _load_file(path, rules, names):
            sources.append(str(path))

    return Engine(rules, sources)


def _load_file(path: Path, rules: list[Rule], names: set[str]) -> bool:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        print(f"hooks: failed to parse {path}: {exc}", file=sys.stderr)
        return False

    if not isinstance(raw, dict) or not isinstance(raw.get("hooks"), list):
        print(f'hooks: {path}: expected top-level "hooks" list', file=sys.stderr)
        return False

    for index, item in enumerate(raw["hooks"]):
        rule = _compile_rule(str(path), index, item)
        if rule is None:
            continue
        if rule.name in names:
            print(
                f'hook "{rule.name}": duplicate name, skipped ({path})',
                file=sys.stderr,
            )
            continue
        names.add(rule.name)
        rules.append(rule)
    return True


def _compile_rule(source: str, index: int, raw: Any) -> Rule | None:
    if not isinstance(raw, dict):
        _error(f"hook #{index}", "rule must be a mapping")
        return None

    name_value = raw.get("name")
    name = name_value.strip() if isinstance(name_value, str) else ""
    label = f'hook "{name}"' if name else f"hook #{index}"
    if not name:
        _error(label, "missing name, skipped")
        return None

    event_value = raw.get("event")
    if not isinstance(event_value, str):
        _error(label, "missing event, skipped")
        return None
    event = parse_event(event_value)
    if event is None:
        _error(label, f'unknown event "{event_value}", skipped')
        return None

    async_mode = raw.get("async", False)
    if not isinstance(async_mode, bool):
        _error(label, "async must be a boolean, skipped")
        return None
    if async_mode and is_blocking(event):
        _error(label, "async not allowed for blocking events, skipped")
        return None

    only_once = raw.get("only_once", False)
    if not isinstance(only_once, bool):
        _error(label, "only_once must be a boolean, skipped")
        return None

    try:
        timeout_s = _parse_duration(raw.get("timeout", "30s"))
    except HookConfigError as exc:
        _error(label, f"{exc}, skipped")
        return None

    try:
        condition = _compile_condition(raw.get("if"))
    except HookConfigError as exc:
        _error(label, f"invalid if: {exc}, skipped")
        return None

    try:
        action = _compile_action(raw.get("action"))
    except HookConfigError as exc:
        _error(label, f"invalid action: {exc}, skipped")
        return None

    return Rule(
        name=name,
        event=event,
        action=action,
        condition=condition,
        only_once=only_once,
        asyncio_mode=async_mode,
        timeout_s=timeout_s,
        source=source,
    )


def _compile_condition(raw: Any) -> Condition | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HookConfigError("if must be a mapping")

    has_all = "all_of" in raw
    has_any = "any_of" in raw
    if has_all and has_any:
        raise HookConfigError("all_of and any_of are mutually exclusive")
    if not has_all and not has_any:
        raise HookConfigError("expected all_of or any_of")

    mode = CombineMode.ALL_OF if has_all else CombineMode.ANY_OF
    atoms_raw = raw[mode.value]
    if not isinstance(atoms_raw, list) or not atoms_raw:
        raise HookConfigError(f"{mode.value} must be a non-empty list")

    atoms: list[AtomCondition] = []
    for item in atoms_raw:
        if not isinstance(item, dict):
            raise HookConfigError("condition atom must be a mapping")
        field = item.get("field")
        if not isinstance(field, str) or not field.strip():
            raise HookConfigError("condition atom missing field")
        match = item.get("match")
        if not isinstance(match, dict):
            raise HookConfigError(f"condition atom {field!r} missing match")
        atoms.append(AtomCondition(field.strip(), _compile_match(match)))
    return Condition(mode, atoms)


def _compile_match(raw: dict) -> Matcher:
    kind = raw.get("type")
    if kind not in _MATCH_TYPES:
        raise HookConfigError(f"unknown match type {kind!r}")

    if kind == "not":
        inner = raw.get("inner")
        if not isinstance(inner, dict):
            raise HookConfigError("not matcher missing inner")
        return NotMatcher(_compile_match(inner))

    value = raw.get("value")
    if not isinstance(value, str):
        raise HookConfigError(f"{kind} matcher missing value")
    if kind == "exact":
        return ExactMatcher(value)
    if kind == "glob":
        return GlobMatcher(value, is_command=False)
    try:
        return RegexMatcher(value, re.compile(value))
    except re.error as exc:
        raise HookConfigError(f"invalid regex: {exc}") from exc


def _compile_action(raw: Any) -> Action:
    if not isinstance(raw, dict):
        raise HookConfigError("action must be a mapping")
    kind = raw.get("type")
    if not isinstance(kind, str):
        raise HookConfigError("action missing type")

    if kind == ActionType.SHELL:
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            raise HookConfigError("shell action missing command")
        return Action(type=ActionType.SHELL, shell=ShellAction(command.strip()))

    if kind == ActionType.PROMPT:
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HookConfigError("prompt action missing text")
        return Action(type=ActionType.PROMPT, prompt=PromptAction(text))

    if kind == ActionType.HTTP:
        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            raise HookConfigError("http action missing url")
        method = raw.get("method", "POST")
        if not isinstance(method, str) or not method.strip():
            raise HookConfigError("http method must be a string")
        headers_raw = raw.get("headers", {})
        if not isinstance(headers_raw, dict):
            raise HookConfigError("http headers must be a mapping")
        headers = {str(k): str(v) for k, v in headers_raw.items()}
        body = raw.get("body")
        if body is not None and not isinstance(body, str):
            raise HookConfigError("http body must be a string")
        return Action(
            type=ActionType.HTTP,
            http=HttpAction(
                url=url.strip(), method=method.strip().upper(), headers=headers, body=body
            ),
        )

    if kind == ActionType.SUBAGENT:
        agent_name = raw.get("agent_name")
        prompt = raw.get("prompt")
        if not isinstance(agent_name, str) or not agent_name.strip():
            raise HookConfigError("subagent action missing agent_name")
        if not isinstance(prompt, str) or not prompt.strip():
            raise HookConfigError("subagent action missing prompt")
        return Action(
            type=ActionType.SUBAGENT,
            subagent=SubagentAction(agent_name=agent_name.strip(), prompt=prompt),
        )

    raise HookConfigError(f"unknown action type {kind!r}")


def _parse_duration(value: Any) -> float:
    if isinstance(value, bool):
        raise HookConfigError("timeout must be a duration")
    if isinstance(value, (int, float)):
        if value < 0:
            raise HookConfigError("timeout must be >= 0")
        return float(value)
    if not isinstance(value, str):
        raise HookConfigError("timeout must be a duration")
    match = _DURATION_RE.match(value)
    if match is None:
        raise HookConfigError(f"invalid timeout {value!r}")
    amount = float(match.group(1))
    unit = match.group(2)
    multiplier = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return amount * multiplier


def _error(label: str, message: str) -> None:
    print(f"{label}: {message}", file=sys.stderr)


__all__ = [
    "HookConfigError",
    "PROJECT_HOOKS_FILE",
    "USER_HOOKS_FILE",
    "load",
]
