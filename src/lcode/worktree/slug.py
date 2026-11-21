"""Slug validation and filesystem-safe flattening for worktrees."""

from __future__ import annotations

import re

MAX_SLUG_LENGTH = 64
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_slug(name: str) -> None:
    # 防路径遍历：只允许安全字符，拒绝 "."/".." 段与空段。
    if not name:
        raise ValueError("Worktree 名称不能为空")
    if len(name) > MAX_SLUG_LENGTH:
        raise ValueError(f"Worktree 名称过长（上限 {MAX_SLUG_LENGTH}）: {name!r}")
    if name.startswith("/") or name.endswith("/"):
        raise ValueError(f"Worktree 名称不能以 / 开头或结尾: {name!r}")
    if "//" in name:
        raise ValueError(f"Worktree 名称不能包含连续 //: {name!r}")
    for segment in name.split("/"):
        if segment in (".", ".."):
            raise ValueError(f"Worktree 名称不能包含 . 或 .. 段: {name!r}")
        if _SEGMENT_RE.match(segment) is None:
            raise ValueError(f"Worktree 名称含非法字符: {segment!r}")


def flat_slug(name: str) -> str:
    # 分支/目录名不能有 D/F 冲突的 "/"，统一替换成 "+"。
    return name.replace("/", "+")


__all__ = ["MAX_SLUG_LENGTH", "flat_slug", "validate_slug"]
