"""The Agent tool: launch definition-based or forked sub-agents."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from mewcode.agent.fork import build_forked_messages, is_fork_context
from mewcode.agent.team_hook import TeamSpawnRequest
from mewcode.agent.tool_context import ToolCallContext, current_tool_context
from mewcode.conversation import Conversation
from mewcode.subagent import Catalog, Definition
from mewcode.task import Manager, PartialState
from mewcode.task.manager import AUTO_BACKGROUND_SECONDS
from mewcode.tool import Result
from mewcode.tool.filter import FilterParams, apply_agent_tool_filter
from mewcode.worktree import Manager as WorktreeManager

FORK_NESTED_ERROR = "Fork 子 Agent 不能再启动 Agent（检测到 fork boilerplate）"
SUBAGENT_NESTED_ERROR = "subagent cannot spawn Agent"


@dataclass
class AgentArgs:
    prompt: str
    description: str
    subagent_type: str = ""
    model: str = ""
    run_in_background: bool = False
    name: str = ""
    team_name: str = ""


def parse_agent_args(args: str | dict) -> AgentArgs:
    if isinstance(args, dict):
        data = args
    else:
        try:
            data = json.loads(args or "{}")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"参数不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("参数必须是 JSON 对象")
    return AgentArgs(
        prompt=str(data.get("prompt", "")),
        description=str(data.get("description", "")),
        subagent_type=str(data.get("subagent_type", "")),
        model=str(data.get("model", "")),
        run_in_background=bool(data.get("run_in_background", False)),
        name=str(data.get("name", "")),
        team_name=str(data.get("team_name", "")),
    )


class AgentTool:
    # 子 Agent 构造入口；主 Agent 通过它把独立任务分发到新的上下文。
    read_only = False
    is_system = True

    def __init__(
        self,
        catalog: Catalog,
        task_mgr: Manager,
        parent=None,
        bg_enabled: bool = True,
        worktree_mgr: WorktreeManager | None = None,
        team_hook=None,
    ) -> None:
        self._catalog = catalog
        self._task_mgr = task_mgr
        self._parent = parent
        self._parent_conv: Conversation | None = None
        self._bg_enabled = bg_enabled
        self._worktree_mgr = worktree_mgr
        self._team_hook = team_hook

    # ── Tool protocol ────────────────────────────────────────────────────

    def name(self) -> str:
        return "Agent"

    def description(self) -> str:
        names = ", ".join(definition.name for definition in self._catalog.list())
        return (
            "把独立任务交给子 Agent 执行。subagent_type 可选值: "
            f"{names}。留空表示 Fork 当前对话并后台执行。"
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "交给子 Agent 的任务指令"},
                "description": {"type": "string", "description": "一句话任务描述"},
                "subagent_type": {
                    "type": "string",
                    "description": "预定义角色名；留空走 Fork 路径",
                },
                "model": {
                    "type": "string",
                    "enum": ["haiku", "sonnet", "opus", "inherit"],
                    "description": "模型覆盖（本期沿用父 provider）",
                },
                "run_in_background": {"type": "boolean", "default": False},
                "team_name": {"type": "string", "description": "非空时把任务派给 Team 队员"},
                "name": {"type": "string", "description": "后台任务名，供 SendMessage 使用"},
            },
            "required": ["prompt", "description"],
        }

    def set_parent(self, agent: Any, conv: Conversation | None = None) -> None:
        # cli/TUI 可在启动后回填；生产路径优先使用 contextvars 里的调用上下文。
        self._parent = agent
        self._parent_conv = conv

    async def execute(self, args: str | dict) -> Result:
        try:
            a_args = parse_agent_args(args)
        except ValueError as exc:
            return Result(content=str(exc), is_error=True)
        if not a_args.prompt:
            return Result(content="prompt is required", is_error=True)
        if not a_args.description:
            return Result(content="description is required", is_error=True)

        ctx = current_tool_context()
        if ctx is None:
            ctx = self._fallback_context()
        if ctx is None:
            return Result(content="Agent 工具缺少调用上下文", is_error=True)

        # ── Team spawn 分支 ────────────────────────────────────────────
        if a_args.team_name:
            if self._team_hook is None:
                return Result(content="Team 功能未启用", is_error=True)
            team_name, member_name, in_process = self._team_hook.is_teammate_context(ctx)
            if team_name:
                if in_process:
                    return Result(
                        content="InProcessTeammateNoSpawnError: "
                        "in-process 队员不能继续 spawn Team 成员",
                        is_error=True,
                    )
                return Result(
                    content="队员不能向 Team 添加成员（team_name 已屏蔽）",
                    is_error=True,
                )
            if ctx.is_subagent:
                return Result(content="只有 Team Lead 可以创建队员", is_error=True)
            request = TeamSpawnRequest(
                team_name=a_args.team_name,
                member_name=a_args.name,
                agent_type=a_args.subagent_type,
                model=a_args.model,
                prompt=a_args.prompt,
                parent_agent=ctx.agent,
                parent_conv=ctx.conv,
            )
            try:
                content = await self._team_hook.spawn_teammate(request)
            except Exception as exc:  # noqa: BLE001 - spawn failure is a tool error
                return Result(content=str(exc), is_error=True)
            return Result(content=content)

        # ── 嵌套阻断 ────────────────────────────────────────────────────
        # Team 队员（Pane/in-process）允许派普通子 Agent，但不能再派 Team 成员。
        is_teammate = getattr(ctx.agent, "teammate_context", None) is not None
        if ctx.is_subagent and not is_teammate:
            message = FORK_NESTED_ERROR if ctx.is_fork else SUBAGENT_NESTED_ERROR
            return Result(content=message, is_error=True)
        if is_fork_context(ctx.conv.messages()):
            return Result(content=FORK_NESTED_ERROR, is_error=True)

        # ── 解析定义 ────────────────────────────────────────────────────
        if a_args.subagent_type:
            definition = self._catalog.resolve(a_args.subagent_type)
            if definition is None:
                return Result(
                    content=f"未知 subagent_type: {a_args.subagent_type}",
                    is_error=True,
                )
        else:
            definition = self._catalog.fork_definition()

        is_fork = definition.is_fork()
        isolation = definition.isolation == "worktree"
        if isolation:
            if self._worktree_mgr is None:
                return Result(content="worktree manager not configured", is_error=True)
            # 本期最小实现：isolation:worktree 强制前台，忽略 background 字段。
            background = False
        else:
            background = is_fork or definition.background or a_args.run_in_background
            if background and not self._bg_enabled:
                message = "后台禁用，无法 Fork" if is_fork else "后台模式已被配置禁用"
                return Result(content=message, is_error=True)

        if is_teammate:
            teammate = ctx.agent.teammate_context
            if teammate.backend_type == "in-process" and background:
                return Result(
                    content="in-process 队员只允许同步子 Agent",
                    is_error=True,
                )

        # ── 工具过滤 + 子 Agent 构造 ────────────────────────────────────
        allowed = self._filter_tools(ctx, definition, background=background)
        sub_agent = self._build_subagent(ctx, definition, allowed)
        sub_conv, task_text = self._build_conversation(ctx, definition, a_args.prompt)

        # ── 后台 / 前台 ────────────────────────────────────────────────
        if background:
            task_id = await self._task_mgr.launch(
                sub_agent,
                sub_conv,
                a_args.name,
                task_text,
                display_task=a_args.prompt,
            )
            return Result(
                content=json.dumps(
                    {"task_id": task_id, "status": "async_launched"},
                    ensure_ascii=False,
                )
            )

        events: asyncio.Queue = asyncio.Queue()
        partial = PartialState()
        pump = asyncio.create_task(self._pump_events(events, partial))

        if isolation:
            from mewcode.agent.agent_worktree import _execute_with_worktree

            try:
                final_text = await _execute_with_worktree(
                    self._worktree_mgr,  # type: ignore[arg-type]
                    definition,
                    sub_agent,
                    sub_conv,
                    a_args.prompt,
                    events,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                return Result(content=f"subagent error: {exc}", is_error=True)
            finally:
                pump.cancel()
            return Result(content=final_text)

        running = asyncio.create_task(sub_agent.run_to_completion(sub_conv, task_text, events))
        try:
            if self._bg_enabled:
                final_text = await asyncio.wait_for(
                    asyncio.shield(running), timeout=AUTO_BACKGROUND_SECONDS
                )
            else:
                final_text = await running
        except TimeoutError:
            pump.cancel()
            task_id = await self._task_mgr.adopt_running(
                sub_agent, sub_conv, a_args.name, events, running, partial
            )
            return Result(
                content=json.dumps(
                    {"task_id": task_id, "status": "timed_out_to_background"},
                    ensure_ascii=False,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - sub-agent failure becomes tool result
            return Result(content=f"subagent error: {exc}", is_error=True)
        finally:
            pump.cancel()
        return Result(content=final_text)

    # ── internals ────────────────────────────────────────────────────────

    def _fallback_context(self) -> ToolCallContext | None:
        if self._parent is None:
            return None
        conv = self._parent_conv or Conversation()
        return ToolCallContext(
            agent=self._parent,
            conv=conv,
            is_subagent=bool(getattr(self._parent, "is_subagent", False)),
            is_fork=is_fork_context(conv.messages()),
        )

    def _filter_tools(
        self, ctx: ToolCallContext, definition: Definition, *, background: bool
    ) -> list[str]:
        all_names = [item.name for item in ctx.agent.registry.definitions()]
        return apply_agent_tool_filter(
            FilterParams(
                all=all_names,
                source=int(definition.source),
                background=background,
                allowed=definition.tools,
                disallowed=definition.disallowed_tools,
                keep_agent=definition.is_fork(),
            )
        )

    def _build_subagent(
        self, ctx: ToolCallContext, definition: Definition, allowed: list[str]
    ) -> Any:
        from mewcode.agent import Agent
        from mewcode.agent.runtime import SessionRuntime

        parent = ctx.agent
        # Fork 路径要与父系统提示逐字一致（prompt cache，N2）；定义式会被 system_prompt 覆盖。
        sub_runtime = SessionRuntime(
            instruction_text=parent.runtime.instruction_text,
            memory_text=parent.runtime.memory_text,
            session=parent.runtime.session,
            context_window=200_000,
            skill_catalog=parent.runtime.skill_catalog,
        )
        return Agent(
            provider=parent.provider,
            registry=parent.registry,
            version=parent.version,
            engine=parent.engine,
            runtime=sub_runtime,
            hook_engine=parent.hook_engine,
            system_prompt=definition.system_prompt or None,
            max_turns=definition.max_turns,
            permission_mode=definition.permission_mode,
            dont_ask=definition.dont_ask,
            approval_upgrader=self._task_mgr.upgrade_approval,
            allowed_tools=allowed,
            is_subagent=True,
        )

    def _build_conversation(
        self, ctx: ToolCallContext, definition: Definition, prompt: str
    ) -> tuple[Conversation, str]:
        if not definition.is_fork():
            return Conversation(), prompt
        forked = build_forked_messages(ctx.conv.messages(), prompt)
        # Fork 的 user 消息（含任务）已在 build_forked_messages 中追加。
        return Conversation.from_messages(forked), ""

    async def _pump_events(self, events: asyncio.Queue, partial: PartialState) -> None:
        while True:
            event = await events.get()
            await self._task_mgr.consume_event(event, partial)


__all__ = ["AgentArgs", "AgentTool", "parse_agent_args"]
