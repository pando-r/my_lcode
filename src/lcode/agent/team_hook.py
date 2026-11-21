"""Team integration types injected into agents without importing team package."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class IncomingMessage:
    from_: str
    text: str
    type: str = "text"
    request_id: str = ""
    approve: bool | None = None
    timestamp: str = ""


@dataclass
class TeammateContext:
    team_name: str
    member_name: str
    agent_id: str
    backend_type: str = "in-process"
    read_unread: Callable[[], Awaitable[tuple[list[int], list[IncomingMessage]]]] | None = None
    mark_read: Callable[[list[int]], Awaitable[None]] | None = None


@dataclass
class TeamSpawnRequest:
    team_name: str
    member_name: str = ""
    agent_type: str = ""
    model: str = ""
    prompt: str = ""
    plan_mode_required: bool = False
    parent_agent: Any = None
    parent_conv: Any = None


class TeamHook(Protocol):
    async def spawn_teammate(self, req: TeamSpawnRequest) -> str: ...

    def is_teammate_context(self, ctx: Any) -> tuple[str, str, bool]: ...


_current_teammate: ContextVar[TeammateContext | None] = ContextVar(
    "lcode_teammate_context", default=None
)


def current_teammate_context() -> TeammateContext | None:
    return _current_teammate.get()


@contextmanager
def with_teammate_context(ctx: TeammateContext | None) -> Iterator[None]:
    if ctx is None:
        yield
        return
    token = _current_teammate.set(ctx)
    try:
        yield
    finally:
        _current_teammate.reset(token)


__all__ = [
    "IncomingMessage",
    "TeamHook",
    "TeamSpawnRequest",
    "TeammateContext",
    "current_teammate_context",
    "with_teammate_context",
]
