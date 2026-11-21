from __future__ import annotations

import asyncio

from lcode.agent import ApprovalRequest, Event
from lcode.conversation import Conversation
from lcode.permission import Outcome
from lcode.task import Manager, PartialState, Status


class FakeAgent:
    def __init__(self, results=("ok",), error=None, delay=0.0) -> None:
        self.results = list(results)
        self.error = error
        self.delay = delay
        self.calls: list[str] = []

    async def run_to_completion(self, conv, task="", events=None):
        self.calls.append(task)
        if task:
            conv.add_assistant("assistant:" + task)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]


async def _done(manager: Manager) -> str:
    return await asyncio.wait_for(manager.subscribe_done().get(), timeout=2)


async def test_launch_completes_and_notifies():
    manager = Manager()
    conv = Conversation()
    task_id = await manager.launch(FakeAgent(["result"]), conv, "worker", "do it")

    assert await _done(manager) == task_id
    task = manager.get(task_id)
    assert task is not None
    assert task.status is Status.COMPLETED
    assert task.result == "result"
    assert task.name == "worker"
    assert task.task == "do it"
    assert task.end_time >= task.start_time
    assert manager.get_by_name("worker") is task


async def test_launch_failure_is_contained():
    manager = Manager()
    task_id = await manager.launch(FakeAgent(error=RuntimeError("boom")), Conversation())
    assert await _done(manager) == task_id
    task = manager.get(task_id)
    assert task.status is Status.FAILED
    assert isinstance(task.err, RuntimeError)
    assert "boom" in str(task.err)


async def test_stop_cancels_running_task():
    manager = Manager()
    fake = FakeAgent(["never"], delay=10)
    task_id = await manager.launch(fake, Conversation())

    assert await manager.stop(task_id) is True
    assert await _done(manager) == task_id
    assert manager.get(task_id).status is Status.CANCELLED
    assert await manager.stop("missing") is False


async def test_send_message_resumes_completed_task():
    manager = Manager()
    conv = Conversation()
    fake = FakeAgent(["one", "two"])
    task_id = await manager.launch(fake, conv, "worker", "first")
    await _done(manager)
    assert manager.get(task_id).status is Status.COMPLETED

    resumed = await manager.send_message("worker", "second")
    assert resumed == task_id
    assert await _done(manager) == task_id
    task = manager.get(task_id)
    assert task.status is Status.COMPLETED
    assert task.result == "two"
    assert [m.content for m in task.conv.messages() if m.role == "user"] == ["second"]
    assert fake.calls == ["first", ""]


async def test_send_message_rejects_unknown_or_busy():
    manager = Manager()
    try:
        await manager.send_message("ghost", "hi")
    except Exception as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected TaskNotFound")

    task_id = await manager.launch(FakeAgent(delay=10), Conversation(), "busy")
    try:
        await manager.send_message("busy", "hi")
    except Exception as exc:
        assert "cannot send" in str(exc)
    else:
        raise AssertionError("expected TaskBusy")
    await manager.stop(task_id)
    await _done(manager)


async def test_name_override_points_to_latest():
    manager = Manager()
    first = await manager.launch(FakeAgent(), Conversation(), "same")
    await _done(manager)
    second = await manager.launch(FakeAgent(), Conversation(), "same")
    await _done(manager)
    assert first != second
    assert manager.get_by_name("same").id == second


async def test_list_and_list_active():
    manager = Manager()
    task_id = await manager.launch(FakeAgent(delay=10), Conversation(), "a")
    assert [task.id for task in manager.list_active()] == [task_id]
    await manager.stop(task_id)
    await _done(manager)
    assert manager.list_active() == []
    assert [task.id for task in manager.list()] == [task_id]


async def test_adopt_running_continues_and_copies_partial():
    manager = Manager()
    conv = Conversation()
    release = asyncio.Event()

    async def running():
        await release.wait()
        return "adopted"

    handle = asyncio.create_task(running())
    events: asyncio.Queue = asyncio.Queue()
    partial = PartialState(tool_count=3, last_activity="bash")
    task_id = await manager.adopt_running(FakeAgent(), conv, "adopted", events, handle, partial)

    release.set()
    assert await _done(manager) == task_id
    task = manager.get(task_id)
    assert task.status is Status.COMPLETED
    assert task.result == "adopted"
    assert task.tool_count == 3
    assert task.last_activity == "bash"


async def test_name_registry_and_done_callback():
    from lcode.team.registry import AgentNameRegistry

    manager = Manager()
    registry = AgentNameRegistry()
    manager.set_name_registry(registry)
    seen: list[str] = []

    async def on_done(task_id: str) -> None:
        seen.append(task_id)

    manager.on_task_done(on_done)
    task_id = await manager.launch(FakeAgent(["ok"]), Conversation(), "worker")
    await _done(manager)
    assert registry.resolve("worker") == task_id
    assert manager.get_by_name("worker").id == task_id
    assert seen == [task_id]


async def test_approval_handler_resolves_event():
    manager = Manager()

    async def handler(req):
        return Outcome.ALLOW_ONCE

    manager.set_approval_handler(handler)
    future = asyncio.get_running_loop().create_future()
    req = ApprovalRequest(name="bash", args="", reason="ask", respond=future)
    await manager.consume_event(Event(approval=req), PartialState())
    assert future.result() is Outcome.ALLOW_ONCE


async def test_without_handler_approval_is_denied():
    manager = Manager()
    future = asyncio.get_running_loop().create_future()
    req = ApprovalRequest(name="bash", args="", reason="ask", respond=future)
    await manager.consume_event(Event(approval=req), PartialState())
    assert future.result() is Outcome.DENY_ONCE
