"""Team name sanitizing, atomic JSON persistence, and cross-process reload."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from mewcode.team.types import Team

_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def sanitize(name: str) -> str:
    text = _SANITIZE_RE.sub("-", name or "").strip("-")
    return text


def atomic_write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def reload_from_disk_locked(team: Team) -> None:
    # 调用方必须持有 team._lock；Pane 子进程与 Lead 各自有内存副本，写前先合并 disk。
    try:
        data = read_json(team.config_path)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict) or not isinstance(data.get("members"), list):
        return
    from mewcode.team.types import TeammateInfo

    team.members = [TeammateInfo.from_dict(item) for item in data["members"]]


def save_team(team: Team) -> None:
    atomic_write_json(team.config_path, team.to_dict())


__all__ = [
    "atomic_write_json",
    "read_json",
    "reload_from_disk_locked",
    "sanitize",
    "save_team",
]
