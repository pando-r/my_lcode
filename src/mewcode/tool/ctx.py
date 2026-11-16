"""Explicit per-call working directory passed to tools via contextvars."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_ctx_cwd: ContextVar[str | None] = ContextVar("mewcode_tool_cwd", default=None)


@contextmanager
def with_cwd(directory: str) -> Iterator[None]:
    # 空目录表示沿用进程 cwd；不改变 os.getcwd()，避免并发同步点。
    if not directory:
        yield
        return
    token = _ctx_cwd.set(directory)
    try:
        yield
    finally:
        _ctx_cwd.reset(token)


def cwd_from_ctx() -> str | None:
    return _ctx_cwd.get()


def resolve_path(p: str) -> str:
    base = _ctx_cwd.get() or str(Path.cwd())
    if not p:
        return base
    path = Path(p)
    if path.is_absolute():
        return str(path)
    return str(Path(base) / path)


__all__ = ["cwd_from_ctx", "resolve_path", "with_cwd"]
