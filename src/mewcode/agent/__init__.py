"""ReAct agent loop orchestration with permission checks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum

from mewcode.agent.fork import is_fork_context
from mewcode.agent.permission_upgrade import ApprovalUpgrader
from mewcode.agent.runtime import SessionRuntime
from mewcode.agent.team_hook import TeammateContext, with_teammate_context
from mewcode.agent.team_mailbox import ingest_teammate_mailbox
from mewcode.agent.tool_context import ToolCallContext, bind_tool_context
from mewcode.conversation import Conversation
from mewcode.hook import DispatchResult
from mewcode.hook import Engine as HookEngine
from mewcode.hook import Event as HookEvent
from mewcode.llm import (
    ROLE_USER,
    Message,
    Provider,
    Request,
    System,
    ToolCall,
    ToolResult,
)
from mewcode.memory import Manager as MemoryManager
from mewcode.permission import Decision, Mode, Outcome, persist_local_allow
from mewcode.permission.engine import Engine
from mewcode.prompt import (
    build_system_prompt,
    gather_environment,
    plan_reminder,
    render_active_skills_block,
    render_skills_catalog,
)
from mewcode.skills.adapter import to_prompt_entries, to_prompt_items
from mewcode.tool import DEFAULT_TIMEOUT, Registry

# 三层熔断机制
MAX_ITERATIONS: int = 50
MAX_UNKNOWN_RUN: int = 3
PLAN_REMINDER_INTERVAL: int = 4

# 每 5 个自然轮次触发一次长期记忆提取（F35）。
MEMORY_UPDATE_INTERVAL: int = 5
_MEMORY_KEYWORDS = ("记住", "记忆", "别忘", "remember", "memo")

NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"


# 工具调用阶段，标记开始和结束
class Phase(Enum):
    START = "start"
    END = "end"


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0


@dataclass
class ToolEvent:
    name: str
    args: str = ""
    phase: Phase = Phase.START
    result: str = ""
    is_error: bool = False


# 人工审批通道
@dataclass
class ApprovalRequest:
    name: str
    args: str
    reason: str
    respond: asyncio.Future | None = None


class MaxTurnsReached(Exception):
    # 子 Agent 触达 max_turns：携带最后的 assistant 文本。
    def __init__(self, final_text: str) -> None:
        super().__init__(f"max turns reached: {final_text[:200]}")
        self.final_text = final_text


@dataclass
class Event:
    # A loop event for the TUI to render.

    text: str = ""
    tool: ToolEvent | None = None
    usage: Usage | None = None
    iter: int = 0
    notice: str = ""
    approval: ApprovalRequest | None = None
    done: bool = False
    err: Exception | None = None


class Agent:
    # Executes the ReAct loop with a five-layer permission gate before tool execution.

    # 不触碰到核心抽象
    def __init__(
        self,
        provider: Provider,
        registry: Registry,
        version: str,
        engine: Engine,
        memory_manager: MemoryManager | None = None,
        instruction_text: str = "",
        memory_text: str = "",
        runtime: SessionRuntime | None = None,
        hook_engine: HookEngine | None = None,
        system_prompt: str | None = None,
        max_turns: int = 0,
        permission_mode: Mode | None = None,
        dont_ask: bool = False,
        approval_upgrader: ApprovalUpgrader | None = None,
        allowed_tools: list[str] | None = None,
        is_subagent: bool = False,
        teammate_context: TeammateContext | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._version = version
        self._engine = engine
        # 子 Agent 选项；主 Agent 保持默认（None/0/False）。
        self._system_prompt_override = system_prompt
        self._max_turns = max_turns
        self._permission_mode = permission_mode
        self._dont_ask = dont_ask
        self._approval_upgrader = approval_upgrader
        self._allowed_tools = allowed_tools
        self._is_subagent = is_subagent
        self._teammate_context = teammate_context
        self._system_prompt_suffix = ""
        # Agent 每轮都会重建；跨轮状态（turn_count / 注入文本）放在 SessionRuntime 里。
        if runtime is None:
            runtime = SessionRuntime(
                memory_manager=memory_manager,
                instruction_text=instruction_text,
                memory_text=memory_text,
            )
        self._runtime = runtime
        # Hook 引擎也挂在 runtime 上，跨轮/命令层共享同一份 only_once 状态。
        if hook_engine is None:
            hook_engine = runtime.hook_engine
        self._hook_engine = hook_engine
        if hook_engine is not None:
            runtime.hook_engine = hook_engine
        self._memory_manager = runtime.memory_manager
        self._instruction_text = runtime.instruction_text
        self._memory_text = runtime.memory_text
        # 持有后台记忆更新任务的引用，避免被 GC 回收。
        self._mem_tasks: set[asyncio.Task] = set()

    @property
    def turn_count(self) -> int:
        # 已完成的自然轮次（只统计模型最终回复无工具调用的轮次）。
        return self._runtime.turn_count

    # ── read-only accessors (Agent 工具 / 子 Agent 构造复用) ─────────────
    @property
    def provider(self) -> Provider:
        return self._provider

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def version(self) -> str:
        return self._version

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def hook_engine(self) -> HookEngine | None:
        return self._hook_engine

    @property
    def runtime(self) -> SessionRuntime:
        return self._runtime

    @property
    def is_subagent(self) -> bool:
        return self._is_subagent

    @property
    def teammate_context(self) -> TeammateContext | None:
        return self._teammate_context

    @property
    def permission_mode(self) -> Mode | None:
        return self._permission_mode

    def set_permission_mode(self, mode: Mode | None) -> None:
        self._permission_mode = mode

    def set_allowed_tools(self, tools: list[str] | None) -> None:
        self._allowed_tools = list(tools) if tools is not None else None

    def append_system_prompt(self, suffix: str) -> None:
        if suffix:
            self._system_prompt_suffix += ("\n" if self._system_prompt_suffix else "") + suffix

    async def run(
        self, conv: Conversation, mode: Mode, cancel: asyncio.Event
    ) -> AsyncIterator[Event]:
        if self._permission_mode is not None:
            mode = self._permission_mode #子Agent的角色配置优先
        env = gather_environment(self._version, self._provider.model) #环境信息
        sys = self._system_prompt() #系统提示
        defs = self._definitions(mode) #工具定义
        unknown_run = 0
        max_turns = self._max_turns or MAX_ITERATIONS

        for it in range(1, max_turns + 1):
            yield Event(iter=it) #TUI用它显示"第{it}轮"
            if cancel.is_set():
                self._finish_cancelled(conv)
                return

            # Team 队员：每轮 LLM 请求前读取 mailbox，未读消息进入本轮 reminder。
            if self._teammate_context is not None:
                await ingest_teammate_mailbox(self, mode)

            # PreUserMessage：每轮 LLM 请求前触发；prompt 动作注入本轮 reminder。
            await self._dispatch_hook(
                HookEvent.PRE_USER_MESSAGE,
                mode,
                iter=it,
                prompt=_last_user_text(conv),
            )

            reminder = self._build_reminder(mode, it)

            # 每轮迭代重建 environment：LoadSkill 在上一轮激活的 SOP 本轮即可见。
            env_text = self._environment_text(env)
            box: list[tuple] = []
            async for ev in self._stream_once(
                conv, defs, sys, env_text, reminder, cancel, box, mode
            ):
                yield ev
            text, calls, usage, ok = box[0]

            if not ok:
                if cancel.is_set():
                    self._finish_cancelled(conv)
                else:
                    self._ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                return

            if usage is not None:
                yield Event(
                    usage=Usage(
                        input=usage.input_tokens,
                        output=usage.output_tokens,
                        cache_write=usage.cache_write,
                        cache_read=usage.cache_read,
                    )
                )

            if not calls:
                if not text.strip():
                    text = "（本轮完成。）"
                    yield Event(text=text)
                conv.add_assistant(text)
                self._runtime.turn_count += 1
                # Stop：模型自然结束、TUI 标记 done 之前触发；不能阻塞本轮。
                await self._dispatch_hook(
                    HookEvent.STOP,
                    mode,
                    iter=it,
                    output=text,
                    turn_count=self._runtime.turn_count,
                )
                # 一轮自然结束：按频率/关键词决定是否异步更新长期记忆。
                self._maybe_update_memory(conv)
                yield Event(done=True)
                return

            conv.add_assistant_with_tool_calls(text, calls)
            unknown_run = unknown_run + 1 if self._all_unknown(calls) else 0

            box2: list[tuple] = []
            async for ev in self._execute_batched(calls, mode, cancel, box2, conv):
                yield ev
            results, completed = box2[0]

            conv.add_tool_results(results)

            if not completed:
                self._ensure_assistant_tail(conv, NOTICE_CANCELLED)
                return

            if unknown_run >= MAX_UNKNOWN_RUN:
                yield Event(notice=NOTICE_UNKNOWN_TOOLS)
                self._ensure_assistant_tail(conv, NOTICE_UNKNOWN_TOOLS)
                yield Event(done=True)
                return

        yield Event(notice=NOTICE_MAX_ITER)
        self._ensure_assistant_tail(conv, NOTICE_MAX_ITER)
        yield Event(done=True)

    # ── helpers ──────────────────────────────────────────────────────────

    async def _stream_once(
        self, conv, defs, sys, env_text, reminder, cancel, box, mode
    ) -> AsyncIterator[Event]:
        req = Request(
            messages=conv.messages(),
            tools=defs,
            system=System(stable=sys, environment=env_text),
            reminder=reminder,
        )
        text = ""
        calls: list[ToolCall] = []
        usage = None
        try:
            async for ev in self._provider.stream(req):
                if cancel.is_set():
                    box.append(("", [], None, False))
                    return
                if ev.err is not None:
                    await self._dispatch_hook(
                        HookEvent.NOTIFICATION,
                        mode,
                        kind="stream_error",
                        message=str(ev.err),
                    )
                    yield Event(err=ev.err)
                    box.append(("", [], None, False))
                    return
                if ev.usage is not None:
                    usage = ev.usage
                if ev.tool_calls:
                    calls.extend(ev.tool_calls)
                if ev.text:
                    text += ev.text
                    yield Event(text=ev.text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await self._dispatch_hook(
                HookEvent.NOTIFICATION,
                mode,
                kind="stream_error",
                message=str(exc),
            )
            yield Event(err=exc)
            box.append(("", [], None, False))
            return
        box.append((text, calls, usage, True))

    async def _execute_batched(
        self, calls, mode, cancel, box, conv: Conversation
    ) -> AsyncIterator[Event]:
        results: list[ToolResult | None] = [None] * len(calls)
        i = 0
        while i < len(calls):
            if cancel.is_set():
                self._fill_cancelled(calls, results, i)
                box.append((self._finalize(results), False))
                return

            if self._registry.is_read_only(calls[i].name):
                j = i
                while j < len(calls) and self._registry.is_read_only(calls[j].name):
                    j += 1
                # PreToolUse 先于权限检查；被 hook 拦截的工具不执行、不弹审批。
                hook_blocks: dict[int, DispatchResult] = {}
                for k in range(i, j):
                    pre = await self._dispatch_hook(
                        HookEvent.PRE_TOOL_USE,
                        mode,
                        **_tool_payload(calls[k]),
                    )
                    if pre.blocked:
                        hook_blocks[k] = pre
                decisions = [self._decide(mode, calls[k], True) for k in range(i, j)]
                for k in range(i, j):
                    yield Event(
                        tool=ToolEvent(
                            name=calls[k].name,
                            args=self._args_preview(calls[k].input),
                            phase=Phase.START,
                        )
                    )
                awaitables = []
                for k in range(i, j):
                    blocked = hook_blocks.get(k)
                    if blocked is not None:
                        results[k] = self._hook_blocked_result(calls[k], blocked)
                        continue
                    decision, reason = decisions[k - i]
                    if decision is Decision.DENY:
                        results[k] = ToolResult(
                            tool_call_id=calls[k].id, content=reason, is_error=True
                        )
                    else:
                        awaitables.append(self._run_one(calls[k], results, k, conv))
                if awaitables:
                    await asyncio.gather(*awaitables)
                for k in range(i, j):
                    r = results[k]
                    yield Event(
                        tool=ToolEvent(
                            name=calls[k].name,
                            args=self._args_preview(calls[k].input),
                            phase=Phase.END,
                            result=r.content,
                            is_error=r.is_error,
                        )
                    )
                    await self._post_tool_hook(calls[k], r, mode)
                i = j
            else:
                pre = await self._dispatch_hook(
                    HookEvent.PRE_TOOL_USE,
                    mode,
                    **_tool_payload(calls[i]),
                )
                yield Event(
                    tool=ToolEvent(
                        name=calls[i].name,
                        args=self._args_preview(calls[i].input),
                        phase=Phase.START,
                    )
                )
                if pre.blocked:
                    results[i] = self._hook_blocked_result(calls[i], pre)
                else:
                    decision, reason = self._decide(mode, calls[i], False)
                    if decision is Decision.ALLOW:
                        await self._run_one(calls[i], results, i, conv)
                    elif decision is Decision.DENY:
                        results[i] = ToolResult(
                            tool_call_id=calls[i].id, content=reason, is_error=True
                        )
                    else:  # ASK → human-in-the-loop
                        outcome = await self._ask_approval(calls[i], reason, mode)
                        if outcome is not None:
                            await self._apply_approval(calls[i], outcome, results, i, conv)
                        else:
                            await self._dispatch_hook(
                                HookEvent.NOTIFICATION,
                                mode,
                                kind="approval",
                                tool_name=calls[i].name,
                                reason=reason,
                            )
                            respond: asyncio.Future = asyncio.get_running_loop().create_future()
                            yield Event(
                                approval=ApprovalRequest(
                                    name=calls[i].name,
                                    args=self._args_preview(calls[i].input),
                                    reason=reason,
                                    respond=respond,
                                )
                            )
                            outcome = await respond
                            await self._apply_approval(calls[i], outcome, results, i, conv)
                r = results[i]
                yield Event(
                    tool=ToolEvent(
                        name=calls[i].name,
                        args=self._args_preview(calls[i].input),
                        phase=Phase.END,
                        result=r.content,
                        is_error=r.is_error,
                    )
                )
                await self._post_tool_hook(calls[i], r, mode)
                i += 1

            if cancel.is_set():
                self._fill_cancelled(calls, results, i)
                box.append((self._finalize(results), False))
                return

        box.append((self._finalize(results), True))

    async def run_to_completion(
        self,
        conv: Conversation,
        task: str = "",
        events: asyncio.Queue | None = None,
    ) -> str:
        # 子 Agent 的 RunToCompletion 循环：复用 run，内部消费事件，返回最终文本。
        if task:
            conv.add_user(task)
        mode = self._permission_mode or Mode.DEFAULT
        cancel = asyncio.Event()
        max_reached = False
        try:
            async for ev in self.run(conv, mode, cancel):
                if ev.err is not None:
                    raise ev.err
                if ev.approval is not None and self._approval_upgrader is None:
                    # 没有人工通道时必须显式拒绝，避免子 Agent 永久悬挂。
                    if ev.approval.respond is not None and not ev.approval.respond.done():
                        ev.approval.respond.set_result(Outcome.DENY_ONCE)
                if events is not None:
                    events.put_nowait(ev)
                if ev.notice == NOTICE_MAX_ITER:
                    max_reached = True
                if ev.done:
                    break
        except asyncio.CancelledError:
            raise

        final_text = _last_assistant_text(conv)
        if max_reached:
            raise MaxTurnsReached(final_text)
        return final_text

    # ── permission helpers ───────────────────────────────────────────────

    def _decide(self, mode: Mode, call: ToolCall, read_only: bool) -> tuple[Decision, str]:
        decision, reason = self._engine.check(mode, call, read_only)
        if decision is Decision.ASK and self._dont_ask:
            # dontAsk：规则未命中且需要询问时，子 Agent 自动放行。
            return Decision.ALLOW, reason
        return decision, reason

    async def _ask_approval(self, call: ToolCall, reason: str, mode: Mode) -> Outcome | None:
        if self._approval_upgrader is None:
            return None
        req = ApprovalRequest(
            name=call.name,
            args=self._args_preview(call.input),
            reason=f"[来自 SubAgent] {reason}",
            respond=None,
        )
        try:
            outcome, handled = await self._approval_upgrader(req)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - upgrade failure falls back to default path
            return None
        return outcome if handled else None

    async def _apply_approval(
        self,
        call: ToolCall,
        outcome: Outcome,
        results: list,
        index: int,
        conv: Conversation,
    ) -> None:
        if outcome is Outcome.DENY_ONCE:
            results[index] = ToolResult(tool_call_id=call.id, content="用户拒绝执行", is_error=True)
            return
        if outcome is Outcome.ALLOW_FOREVER:
            try:
                persist_local_allow(self._engine, call)
            except Exception:  # noqa: BLE001 - persistence must not block execution
                pass
        await self._run_one(call, results, index, conv)

    # ── tool filtering ───────────────────────────────────────────────────

    def _definitions(self, mode: Mode):
        defs = (
            self._registry.read_only_definitions()
            if mode is Mode.PLAN
            else self._registry.definitions()
        )
        if self._allowed_tools is None:
            return defs
        allowed = set(self._allowed_tools)
        return [definition for definition in defs if definition.name in allowed]

    def _tool_allowed(self, name: str) -> bool:
        return self._allowed_tools is None or name in self._allowed_tools

    # ── hooks ────────────────────────────────────────────────────────────

    async def _dispatch_hook(self, event: HookEvent, mode: Mode, **extra) -> DispatchResult:
        if self._hook_engine is None:
            return DispatchResult()
        session = self._runtime.session
        payload: dict = {
            "event": event.value,
            "session_id": session.session_id if session is not None else "",
            "cwd": os.getcwd(),
            "mode": str(mode),
        }
        payload.update(extra)
        result = await self._hook_engine.dispatch(event, payload)
        # prompt 动作的文本进入 reminder 队列，只在本轮/下轮 LLM 请求生效，不写入对话历史。
        self._runtime.append_reminders(result.injected_prompts)
        return result

    async def _post_tool_hook(self, call: ToolCall, result: ToolResult, mode: Mode) -> None:
        await self._dispatch_hook(
            HookEvent.POST_TOOL_USE,
            mode,
            **_tool_payload(call),
            tool_result=result.content,
            is_error=result.is_error,
        )

    def _hook_blocked_result(self, call: ToolCall, blocked: DispatchResult) -> ToolResult:
        reason = blocked.reason or "blocked by hook"
        return ToolResult(
            tool_call_id=call.id,
            content=f"[hook {blocked.blocking_hook_name}] {reason}",
            is_error=True,
        )

    def _build_reminder(self, mode: Mode, iteration: int) -> str:
        parts: list[str] = []
        if mode is Mode.PLAN:
            full = iteration == 1 or (iteration - 1) % PLAN_REMINDER_INTERVAL == 0
            parts.append(plan_reminder(full))
        pending = self._runtime.take_reminders()
        if pending:
            parts.append("\n\n".join(pending))
        return "\n\n".join(part for part in parts if part)

    async def _run_one(self, call: ToolCall, results: list, index: int, conv: Conversation) -> None:
        if not self._tool_allowed(call.name):
            # 工具过滤的执行层兜底：即使模型报出被禁用工具也不执行。
            results[index] = ToolResult(
                tool_call_id=call.id,
                content=f"工具不可用: {call.name}",
                is_error=True,
            )
            return
        ctx = ToolCallContext(
            agent=self,
            conv=conv,
            is_subagent=self._is_subagent,
            is_fork=is_fork_context(conv.messages()),
        )
        with bind_tool_context(ctx), with_teammate_context(self._teammate_context):
            r = await self._registry.execute(call.name, call.input, timeout=DEFAULT_TIMEOUT)
        results[index] = ToolResult(tool_call_id=call.id, content=r.content, is_error=r.is_error)

    def _fill_cancelled(self, calls, results, start: int) -> None:
        for k in range(start, len(calls)):
            if results[k] is None:
                results[k] = ToolResult(
                    tool_call_id=calls[k].id, content=NOTICE_CANCELLED, is_error=True
                )

    def _finalize(self, results: list[ToolResult | None]) -> list[ToolResult]:
        return [r for r in results if r is not None]

    def _all_unknown(self, calls: list[ToolCall]) -> bool:
        return all(
            self._registry.get(c.name) is None or not self._tool_allowed(c.name) for c in calls
        )

    def _ensure_assistant_tail(self, conv: Conversation, fallback: str) -> None:
        if conv.last_role() != "assistant":
            conv.add_assistant(fallback)

    def _finish_cancelled(self, conv: Conversation) -> None:
        self._ensure_assistant_tail(conv, NOTICE_CANCELLED)

    # ── skills ───────────────────────────────────────────────────────────

    def activate_skill(self, name: str, prompt_body: str) -> None:
        # SOP 钉到跨轮 runtime；下一轮（或下一迭代）environment 会包含它。
        self._runtime.active_skills.activate(name, prompt_body)

    def clear_active_skills(self) -> None:
        self._runtime.active_skills.clear()

    def set_skill_catalog(self, catalog) -> None:
        # catalog 对象本身可热重载；Agent 只持引用。
        self._runtime.skill_catalog = catalog

    def list_active(self) -> list[str]:
        return self._runtime.active_skills.names()

    def _system_prompt(self) -> str:
        if self._system_prompt_override is not None:
            base = self._system_prompt_override
        else:
            catalog = self._runtime.skill_catalog
            catalog_text = (
                render_skills_catalog(to_prompt_items(catalog.list()))
                if catalog is not None
                else ""
            )
            base = build_system_prompt(self._instruction_text, self._memory_text, catalog_text)
        if not self._system_prompt_suffix:
            return base
        return f"{base}\n\n{self._system_prompt_suffix}"

    def _environment_text(self, env) -> str:
        text = env.render()
        block = render_active_skills_block(to_prompt_entries(self._runtime.active_skills))
        if not block:
            return text
        return f"{text}\n\n{block}" if text else block

    # ── memory ───────────────────────────────────────────────────────────

    def _maybe_update_memory(self, conv: Conversation) -> None:
        # 每 5 轮 或 本轮用户消息含显式记忆关键词 → 异步更新（不阻塞）。
        if self._memory_manager is None:
            return
        recent = _recent_turn(conv.messages())
        if not recent:
            return
        if self.turn_count % MEMORY_UPDATE_INTERVAL != 0 and not _has_memory_signal(recent):
            return
        task = asyncio.create_task(self._memory_manager.update_async(recent))
        self._mem_tasks.add(task)
        task.add_done_callback(self._memory_task_done)

    def _memory_task_done(self, task: asyncio.Task) -> None:
        self._mem_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logging.getLogger(__name__).warning("记忆更新任务异常: %s", exc)

    @staticmethod
    def _args_preview(args: str) -> str:
        args = (args or "").strip()
        if len(args) > 80:
            args = args[:77] + "..."
        return args


def _last_assistant_text(conv: Conversation) -> str:
    for msg in reversed(conv.messages()):
        if msg.role == "assistant" and msg.content.strip():
            return msg.content
    return ""


def _last_user_text(conv: Conversation) -> str:
    for msg in reversed(conv.messages()):
        if msg.role == ROLE_USER:
            return msg.content
    return ""


def _tool_payload(call: ToolCall) -> dict:
    return {"tool_name": call.name, "tool_input": _tool_input_dict(call.input)}


def _tool_input_dict(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _recent_turn(msgs: list[Message]) -> list[Message]:
    # 从最后一条 user 消息到结尾（本轮用户请求 + 模型/工具回复）。
    for index in range(len(msgs) - 1, -1, -1):
        if msgs[index].role == ROLE_USER:
            return msgs[index:]
    return []


def __getattr__(name: str):
    # 懒加载，避免 agent 包初始化时反向依赖 task/subagent 包。
    if name == "AgentTool":
        from mewcode.agent.agent_tool import AgentTool

        return AgentTool
    raise AttributeError(name)


def _has_memory_signal(msgs: list[Message]) -> bool:
    # 显式记忆请求关键词检测（"记住""记忆""别忘""remember""memo"）。
    for msg in msgs:
        if msg.role != ROLE_USER:
            continue
        content = msg.content.lower()
        if any(keyword in content for keyword in _MEMORY_KEYWORDS):
            return True
    return False
