"""Parse Agent role definitions from Markdown + YAML frontmatter."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from lcode.permission import Mode
from lcode.subagent.definition import Definition, ModelName, Source

UTF8_BOM = "﻿"
AGENT_NAME_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9\-_]{0,31}$")
VALID_MODELS = ("haiku", "sonnet", "opus", "inherit")
_PERMISSION_MODES = {
    "default": Mode.DEFAULT,
    "acceptedits": Mode.ACCEPT_EDITS,
    "plan": Mode.PLAN,
    "bypasspermissions": Mode.BYPASS,
}


class AgentParseError(ValueError):
    pass


def parse_frontmatter_and_body(raw: str) -> tuple[dict, str]:
    # 拆出 --- ... --- 之间的 YAML 与正文；缺失/未闭合/非映射都抛 AgentParseError。
    text = raw.lstrip(UTF8_BOM)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise AgentParseError("缺少起始 ---")

    end: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            end = index
            break
    if end is None:
        raise AgentParseError("frontmatter 未闭合")

    try:
        meta = yaml.safe_load("".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise AgentParseError(f"YAML 解析失败: {exc}") from exc
    if not isinstance(meta, dict):
        raise AgentParseError("frontmatter 必须是映射")

    body = "".join(lines[end + 1 :]).lstrip("\r\n")
    return meta, body


def _warn(label: str, message: str) -> None:
    print(f'subagent "{label}": {message}', file=sys.stderr)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _pick(meta: dict, *keys: str) -> object:
    # 兼容 spec 的 camelCase 与用户常用的 snake_case 写法。
    for key in keys:
        if key in meta:
            return meta[key]
    return None


def _string_list(value: object, label: str, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        _warn(label, f"{field_name} must be a list, ignored")
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_permission_mode(value: object, label: str) -> tuple[Mode, bool]:
    # 返回 (mode, dont_ask)；未知值告警并回退 default（AC22）。
    text = _text(value)
    if not text:
        return Mode.DEFAULT, False
    if text.lower() == "dontask":
        return Mode.DEFAULT, True
    mode = _PERMISSION_MODES.get(text.lower())
    if mode is None:
        _warn(label, f'unknown permissionMode "{text}", defaulting to default')
        return Mode.DEFAULT, False
    return mode, False


def _parse_max_turns(value: object, label: str) -> int:
    if value is None or value == "":
        return 0
    try:
        turns = int(value)
    except (TypeError, ValueError):
        _warn(label, f"maxTurns must be an integer, got {value!r}; using default")
        return 0
    return max(0, turns)


def parse_definition(data: bytes, file_path: str, source: Source) -> Definition:
    # 解析一个定义文件；name/description 缺失或非法抛错，其余字段错误降级（AC22）。
    raw = data.decode("utf-8-sig") if isinstance(data, bytes) else str(data)
    meta, body = parse_frontmatter_and_body(raw)

    name = _text(meta.get("name"))
    label = name or file_path
    if not name:
        raise AgentParseError("缺少 name")
    if AGENT_NAME_REGEX.match(name) is None:
        raise AgentParseError(f"非法 name: {name!r}")

    description = _text(meta.get("description"))
    if not description:
        raise AgentParseError("缺少 description")

    model_text = _text(meta.get("model")) or "inherit"
    if model_text not in VALID_MODELS:
        _warn(label, f'unknown model "{model_text}", defaulting to inherit')
        model_text = "inherit"

    permission_mode, dont_ask = _parse_permission_mode(
        _pick(meta, "permissionMode", "permission_mode"), label
    )
    isolation = _text(meta.get("isolation"))
    if isolation not in ("", "worktree"):
        _warn(label, f'unknown isolation "{isolation}", defaulting to none')
        isolation = ""
    return Definition(
        name=name,
        description=description,
        tools=_string_list(meta.get("tools"), label, "tools"),
        disallowed_tools=_string_list(
            _pick(meta, "disallowedTools", "disallowed_tools"),
            label,
            "disallowedTools",
        ),
        model=model_text,  # type: ignore[arg-type]
        max_turns=_parse_max_turns(_pick(meta, "maxTurns", "max_turns"), label),
        permission_mode=permission_mode,
        dont_ask=dont_ask,
        background=bool(meta.get("background", False)),
        isolation=isolation,
        plan_mode_required=bool(_pick(meta, "planModeRequired", "plan_mode_required") or False),
        system_prompt=body,
        file_path=file_path,
        source=source,
    )


def parse_file(path: str | Path, source: Source) -> Definition:
    p = Path(path)
    return parse_definition(p.read_bytes(), str(p.resolve()), source)


__all__ = [
    "AGENT_NAME_REGEX",
    "AgentParseError",
    "ModelName",
    "UTF8_BOM",
    "VALID_MODELS",
    "parse_definition",
    "parse_file",
    "parse_frontmatter_and_body",
]
