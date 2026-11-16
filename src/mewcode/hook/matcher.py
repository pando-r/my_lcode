"""Apply permission matchers to hook payload fields."""

from __future__ import annotations

import json
from typing import Any

from mewcode.hook.rule import AtomCondition, CombineMode, Condition, Payload


def get_by_path(payload: Payload, path: str) -> str:
    # 缺失路径按空串处理；嵌套对象序列化为稳定 JSON 供 regex/glob 使用。
    value: Any = payload
    for part in path.split(".") if path else []:
        if not isinstance(value, dict) or part not in value:
            return ""
        value = value[part]

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return str(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def eval_condition(condition: Condition | None, payload: Payload) -> bool:
    if condition is None:
        return True
    results = [_match_atom(atom, payload) for atom in condition.atoms]
    if condition.mode is CombineMode.ALL_OF:
        return all(results)
    return any(results)


def _match_atom(atom: AtomCondition, payload: Payload) -> bool:
    return atom.matcher.match(get_by_path(payload, atom.field))


__all__ = ["eval_condition", "get_by_path"]
