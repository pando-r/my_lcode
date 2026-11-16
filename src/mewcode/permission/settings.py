"""Settings loading and tool-call mapping helpers."""

# 权限配置加载器·用于加载权限配置文件。
# 加载和映射工具调用的辅助函数。
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mewcode.llm import ToolCall
from mewcode.permission import Category
from mewcode.permission.rule import RuleSet, parse_rule


class SettingsError(Exception):
    # Raised when a settings file is malformed (caller degrades to empty).
    pass


@dataclass
class PermissionsBlock:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class Settings:
    default_mode: str = ""
    permissions: PermissionsBlock = field(default_factory=PermissionsBlock)


def load_settings(path: str) -> Settings:
    p = Path(path)
    if not p.is_file():
        return Settings()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        raise SettingsError(f"无法解析权限配置: {exc}") from exc
    if not isinstance(raw, dict):
        raise SettingsError("权限配置顶层必须是映射")

    s = Settings()
    if isinstance(raw.get("default_mode"), str):
        s.default_mode = raw["default_mode"]
    perms = raw.get("permissions")
    if isinstance(perms, dict):
        for key in ("allow", "deny"):
            val = perms.get(key, [])
            if isinstance(val, list):
                setattr(s.permissions, key, [str(x) for x in val if isinstance(x, str)])
    return s


def to_rule_set(s: Settings) -> RuleSet:
    # 单条规则解析失败只报到 stderr，其余规则照常加载。
    rs = RuleSet()
    for text in s.permissions.allow:
        rule, err = parse_rule(text)
        if err is not None:
            print(f'rule "{text}" parse failed: {err}', file=sys.stderr)
            continue
        assert rule is not None
        rule.allow = True
        rs.allow.append(rule)
    for text in s.permissions.deny:
        rule, err = parse_rule(text)
        if err is not None:
            print(f'rule "{text}" parse failed: {err}', file=sys.stderr)
            continue
        assert rule is not None
        rule.allow = False
        rs.deny.append(rule)
    return rs


def friendly_name(internal: str) -> str:
    table = {
        "bash": "Bash",
        "read_file": "Read",
        "write_file": "Write",
        "edit_file": "Edit",
        "glob": "Glob",
        "grep": "Grep",
    }
    return table.get(internal, internal)


def categorize(internal: str, read_only: bool) -> Category:
    if read_only:
        return Category.READ
    if internal in ("write_file", "edit_file"):
        return Category.WRITE
    return Category.EXEC  # bash and unknown tools: treat as side-effecting (N7)


def extract_target(call: ToolCall) -> tuple[str, bool, bool]:
    # Return (target, is_file, ok).
    data: object
    if isinstance(call.input, str):
        try:
            data = json.loads(call.input or "{}")
        except json.JSONDecodeError:
            return "", False, False
    else:
        data = call.input or {}
    if not isinstance(data, dict):
        return "", False, False

    if call.name in ("read_file", "write_file", "edit_file"):
        path = data.get("path")
        if not isinstance(path, str) or not path:
            return "", True, False
        return path, True, True
    if call.name in ("glob", "grep"):
        path = data.get("path") or "."
        if not isinstance(path, str):
            return "", True, False
        return path, True, True
    if call.name == "bash":
        command = data.get("command")
        if not isinstance(command, str):
            return "", False, False
        return command, False, True
    return "", False, False
