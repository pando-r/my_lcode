"""Shared fork launcher used by Skill fork and other frontends."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lcode.agent.runtime import SessionRuntime
    from lcode.conversation import Conversation
    from lcode.hook.engine import Engine as HookEngine
    from lcode.llm import Provider
    from lcode.permission.engine import Engine
    from lcode.tool import Registry


@dataclass
class ForkLaunchOpts:
    conv: Conversation  # 已含任务 user 消息
    provider: Provider
    registry: Registry
    version: str
    engine: Engine
    runtime: SessionRuntime
    hook_engine: HookEngine | None = None
    allowed_tools: list[str] | None = None
    system_prompt: str | None = None
    max_turns: int = 0
    events_sink: asyncio.Queue | None = None


async def launch_fork(opts: ForkLaunchOpts) -> str:
    # 与 Agent 工具的 Fork 路径共用 Agent 构造与 run_to_completion 循环。
    from lcode.agent import Agent

    agent = Agent(
        provider=opts.provider,
        registry=opts.registry,
        version=opts.version,
        engine=opts.engine,
        runtime=opts.runtime,
        hook_engine=opts.hook_engine,
        system_prompt=opts.system_prompt,
        max_turns=opts.max_turns,
        allowed_tools=opts.allowed_tools,
        is_subagent=True,
    )
    return await agent.run_to_completion(opts.conv, "", opts.events_sink)


__all__ = ["ForkLaunchOpts", "launch_fork"]
