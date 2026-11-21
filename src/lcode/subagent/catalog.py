"""Agent role catalog with builtin / user / project layering."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from lcode.permission import Mode
from lcode.subagent.definition import Definition, Source
from lcode.subagent.embed import builtin_definitions
from lcode.subagent.parser import parse_file

PROJECT_AGENTS_DIR = Path(".lcode") / "agents"
USER_AGENTS_DIR = Path(".lcode") / "agents"
FORK_AGENT_NAME = "__fork__"


class Catalog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._defs: dict[str, Definition] = {}
        self._by_source: dict[Source, list[Definition]] = {source: [] for source in Source}

    def _add_all(self, definitions: list[Definition], source: Source) -> None:
        # 按 builtin → user → project 顺序调用，后加载的同名定义覆盖前者。
        with self._lock:
            for definition in definitions:
                self._defs[definition.name] = definition
                self._by_source[source].append(definition)

    def resolve(self, name: str) -> Definition | None:
        with self._lock:
            return self._defs.get(name)

    def list(self) -> list[Definition]:
        with self._lock:
            return sorted(self._defs.values(), key=lambda d: d.name)

    def list_by_source(self, source: Source) -> list[Definition]:
        with self._lock:
            return sorted(self._by_source[source], key=lambda d: d.name)

    def fork_definition(self) -> Definition:
        # Fork 路径的临时定义：保留 Agent 工具（嵌套由 QuerySource/Boilerplate 拦截）。
        return Definition(
            name=FORK_AGENT_NAME,
            description="Fork-based subagent",
            model="inherit",
            max_turns=25,
            permission_mode=Mode.DEFAULT,
            source=Source.BUILTIN,
        )


def _warn(path: Path, message: str) -> None:
    print(f"subagent {path}: {message}, skipped", file=sys.stderr)


def _load_from_dir(directory: Path, source: Source) -> list[Definition]:
    if not directory.is_dir():
        return []
    definitions: list[Definition] = []
    for path in sorted(directory.glob("*.md")):
        try:
            definitions.append(parse_file(path, source))
        except Exception as exc:  # noqa: BLE001 - one bad file must not block startup
            _warn(path, str(exc))
    return definitions


def load_catalog(root: str | Path) -> Catalog:
    catalog = Catalog()
    catalog._add_all(builtin_definitions(), Source.BUILTIN)
    catalog._add_all(_load_from_dir(Path.home() / USER_AGENTS_DIR, Source.USER), Source.USER)
    catalog._add_all(
        _load_from_dir(Path(root) / PROJECT_AGENTS_DIR, Source.PROJECT), Source.PROJECT
    )
    # 插件来源本期恒为空（F8）。
    return catalog


__all__ = [
    "Catalog",
    "FORK_AGENT_NAME",
    "PROJECT_AGENTS_DIR",
    "USER_AGENTS_DIR",
    "load_catalog",
]
