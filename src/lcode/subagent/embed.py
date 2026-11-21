"""Load built-in Agent definitions shipped inside the package."""

from __future__ import annotations

from importlib.resources import files

from lcode.subagent.definition import Definition, Source
from lcode.subagent.parser import parse_definition


def builtin_definitions() -> list[Definition]:
    # 内置定义解析失败属于代码 bug：启动期直接抛错（N4）。
    package = files("lcode.subagent.builtin")
    definitions: list[Definition] = []
    for entry in package.iterdir():
        if not entry.name.endswith(".md"):
            continue
        definition = parse_definition(entry.read_bytes(), f"builtin:{entry.name}", Source.BUILTIN)
        definitions.append(definition)
    return sorted(definitions, key=lambda d: d.name)


__all__ = ["builtin_definitions"]
