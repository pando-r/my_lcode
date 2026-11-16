"""Background sub-agent task lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import sys
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from mewcode.permission import Outcome

if TYPE_CHECKING:
    from mewcode.agent import Agent, ApprovalRequest, Event
    from mewcode.conversation import Conversation

AUTO_BACKGROUND_SECONDS = 120.0


class Status(IntEnum):
    RUNNING = 0
    COMPLETED = 1
    FAILED = 2
    CANCELLED = 3

    def __str__(self) -> str:
        return self.name.lower()


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0


@dataclass
class PartialState:
    # 前台→后台移交时已收集的中间状态。
    last_assistant_text: str = ""
    tool_count: int = 0
    last_activity: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass
class BackgroundTask:
    id: str
    name: str
    sub_agent: Agent
    conv: Conversation
    task: str
    status: Status = Status.RUNNING
    result: str = ""
    err: BaseException | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float = 0.0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    handle: asyncio.Task | None = None
    usage: Usage = field(default_factory=Usage)
    tool_count: int = 0
    last_activity: str = ""
    cwd: str = ""


class TaskNotFound(Exception):
    pass


class TaskBusy(Exception):
    pass


class Manager:
    # 单事件循环内的后台任务管理器；所有状态变更都通过 asyncio 原语串行化。
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, BackgroundTask] = {}
        self._by_name: dict[str, str] = {}
        self._done: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._approval_handler: Any = None
        self._watchers: set[asyncio.Task] = set()
        self._name_reg: Any = None
        self._done_callbacks: list[Any] = []

    # ── queries ──────────────────────────────────────────────────────────

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list(self) -> list[BackgroundTask]:
        return sorted(self._tasks.values(), key=lambda task: task.start_time)

    def list_active(self) -> list[BackgroundTask]:
        return [task for task in self.list() if task.status is Status.RUNNING]

    def subscribe_done(self) -> asyncio.Queue[str]:
        return self._done

    def get_by_name(self, name: str) -> BackgroundTask | None:
        task_id = None
        if self._name_reg is not None:
            task_id = self._name_reg.resolve(name)
        if task_id is None:
            task_id = self._by_name.get(name)
        return self._tasks.get(task_id) if task_id else None

    def set_name_registry(self, registry: Any) -> None:
        self._name_reg = registry

    def on_task_done(self, callback: Any) -> None:
        self._done_callbacks.append(callback)

    # ── approval escalation ──────────────────────────────────────────────

    def set_approval_handler(self, handler: Any) -> None:
        self._approval_handler = handler

    async def upgrade_approval(self, req: ApprovalRequest) -> tuple[Outcome, bool]:
        # ok=False 表示 TUI 通道不可用，由 Agent 回退到默认 Approval 事件路径。
        if self._approval_handler is None:
            return Outcome.DENY_ONCE, False
        try:
            outcome = await self._approval_handler(req)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - failing UI must not hang the sub-agent
            return Outcome.DENY_ONCE, True
        return outcome, True

    # ── lifecycle ────────────────────────────────────────────────────────

    def _next_id(self) -> str:
        return f"task_{secrets.token_hex(4)}"

    async def launch(
        self,
        ag: Agent,
        conv: Conversation,
        name: str = "",
        task_text: str = "",
        display_task: str | None = None,
        task_id: str | None = None,
        cwd: str = "",
    ) -> str:
        chosen_id = task_id or self._next_id()
        if chosen_id in self._tasks:
            raise ValueError(f"task_id 已存在: {chosen_id}")
        task = BackgroundTask(
            id=chosen_id,
            name=name,
            sub_agent=ag,
            conv=conv,
            task=task_text if display_task is None else display_task,
            cwd=cwd,
        )
        async with self._lock:
            self._tasks[task.id] = task
            if name:
                self._by_name[name] = task.id
                if self._name_reg is not None:
                    self._name_reg.register(name, task.id)

        events: asyncio.Queue = asyncio.Queue()
        aggregator = asyncio.create_task(self._aggregate(events, task))
        running = asyncio.create_task(_run_with_cwd(ag, conv, task_text, events, cwd))
        task.handle = running
        self._watch(task, running, aggregator)
        return task.id

    async def adopt_running(
        self,
        ag: Agent,
        conv: Conversation,
        name: str,
        events: asyncio.Queue,
        handle: asyncio.Task,
        partial: PartialState | None = None,
    ) -> str:
        partial = partial or PartialState()
        task = BackgroundTask(
            id=self._next_id(),
            name=name,
            sub_agent=ag,
            conv=conv,
            task="",
            handle=handle,
            usage=partial.usage,
            tool_count=partial.tool_count,
            last_activity=partial.last_activity,
        )
        async with self._lock:
            self._tasks[task.id] = task
            if name:
                self._by_name[name] = task.id

        aggregator = asyncio.create_task(self._aggregate(events, task))
        self._watch(task, handle, aggregator)
        return task.id

    def _watch(self, task: BackgroundTask, running: asyncio.Task, aggregator: asyncio.Task) -> None:
        watcher = asyncio.create_task(self._await_completion(task, running, aggregator))
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)

    async def _await_completion(
        self, task: BackgroundTask, running: asyncio.Task, aggregator: asyncio.Task
    ) -> None:
        try:
            text = await running
            task.result = text
            task.status = Status.COMPLETED
        except asyncio.CancelledError:
            task.status = Status.CANCELLED
        except BaseException as exc:  # noqa: BLE001 - N3: 子 Agent 崩溃不影响主程序
            task.status = Status.FAILED
            task.err = exc
        finally:
            task.end_time = time.monotonic()
            aggregator.cancel()
            self._notify_done(task.id)
            for callback in list(self._done_callbacks):
                try:
                    await callback(task.id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - observers must not crash tasks
                    print(
                        f"task manager: done callback failed: {exc}",
                        file=sys.stderr,
                    )

    def _notify_done(self, task_id: str) -> None:
        try:
            self._done.put_nowait(task_id)
        except asyncio.QueueFull:
            print(
                f"task manager: done queue full, dropping notification for {task_id}",
                file=sys.stderr,
            )

    async def stop(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        if task.handle is not None and not task.handle.done():
            task.handle.cancel()
        return True

    async def send_message(self, name: str, message: str, *, allow_stopped: bool = False) -> str:
        task = self.get_by_name(name)
        if task is None:
            raise TaskNotFound(f"task name not found: {name}")
        if task.status is Status.RUNNING:
            raise TaskBusy(f"task {task.id} is running, cannot send message")
        if not allow_stopped and task.status is not Status.COMPLETED:
            raise TaskBusy(f"task {task.id} is {task.status}, cannot send message")
        task.conv.add_user(message)
        task.status = Status.RUNNING
        task.result = ""
        task.err = None
        task.end_time = 0.0

        events: asyncio.Queue = asyncio.Queue()
        aggregator = asyncio.create_task(self._aggregate(events, task))
        running = asyncio.create_task(task.sub_agent.run_to_completion(task.conv, "", events))
        task.handle = running
        self._watch(task, running, aggregator)
        return task.id

    # ── event aggregation ────────────────────────────────────────────────

    async def _aggregate(self, events: asyncio.Queue, task: BackgroundTask) -> None:
        while True:
            event = await events.get()
            await self.consume_event(event, task)

    async def consume_event(self, event: Event, target: Any) -> None:
        # 同时服务 BackgroundTask 与前台 PartialState（字段集合一致）。
        if event.approval is not None:
            outcome, handled = await self.upgrade_approval(event.approval)
            respond = event.approval.respond
            if respond is not None and not respond.done():
                respond.set_result(outcome if handled else Outcome.DENY_ONCE)
            return
        if event.usage is not None:
            target.usage.input += event.usage.input
            target.usage.output += event.usage.output
            target.usage.cache_write += event.usage.cache_write
            target.usage.cache_read += event.usage.cache_read
            return
        if event.tool is not None and event.tool.phase.value == "start":
            target.tool_count += 1
            target.last_activity = event.tool.name
        if event.text and hasattr(target, "last_assistant_text"):
            target.last_assistant_text += event.text


async def _run_with_cwd(ag, conv, task_text, events, cwd):
    from mewcode.tool.ctx import with_cwd

    context = with_cwd(cwd) if cwd else contextlib.nullcontext()
    with context:
        return await ag.run_to_completion(conv, task_text, events)


__all__ = [
    "AUTO_BACKGROUND_SECONDS",
    "BackgroundTask",
    "Manager",
    "PartialState",
    "Status",
    "TaskBusy",
    "TaskNotFound",
    "Usage",
]
