"""Persist a human-in-the-loop "always allow" rule into the local settings file."""

# 人在回路「永久」写本地层规则。
from __future__ import annotations

from pathlib import Path

import yaml

from lcode.llm import ToolCall
from lcode.permission.engine import Engine
from lcode.permission.rule import parse_rule
from lcode.permission.settings import extract_target, friendly_name, load_settings


def persist_local_allow(engine: Engine, call: ToolCall) -> None:
    rule_str, ok = rule_for(engine, call)
    if not ok:
        return
    settings = load_settings(engine.local_path)
    if rule_str not in settings.permissions.allow:
        settings.permissions.allow.append(rule_str)
    _write_settings(engine.local_path, settings)

    rule, err = parse_rule(rule_str)
    if err is None and rule is not None:
        rule.allow = True
        if not any(r.tool == rule.tool and r.raw == rule.raw for r in engine.local.allow):
            engine.local.allow.append(rule)


def rule_for(engine: Engine, call: ToolCall) -> tuple[str, bool]:
    friendly = friendly_name(call.name)
    target, is_file, ok = extract_target(call)
    if not ok or not target:
        return "", False
    if is_file:
        rel = _relpath(engine.root, target)
        return f"{friendly}({rel})", True
    return f"{friendly}({_escape_glob(target)})", True


def _relpath(root: str, target: str) -> str:
    p = Path(target)
    if not p.is_absolute():
        p = Path(root) / p
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return str(p)


def _escape_glob(s: str) -> str:
    # 转义 glob 元字符与规则前缀（=~!），确保自动持久化的规则按字面量匹配。
    escaped = "".join(f"\\{ch}" if ch in "\\*?" else ch for ch in s)
    if escaped and escaped[0] in "=~!":
        escaped = "\\" + escaped
    return escaped


def _write_settings(path: str, settings) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "default_mode": settings.default_mode or "default",
        "permissions": {
            "allow": settings.permissions.allow,
            "deny": settings.permissions.deny,
        },
    }
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
