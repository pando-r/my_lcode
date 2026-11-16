from __future__ import annotations

import asyncio
import json

from mewcode.conversation import Conversation
from mewcode.task import (
    Manager,
    SendMessageTool,
    Status,
    TaskGetTool,
    TaskListTool,
    TaskStopTool,
)


class FakeAgent:
    def __init__(self, results=("ok",), delay=0.0) -> None:
        self.results = list(results)
        self.delay = delay
        self.calls = 0

    async def run_to_completion(self, conv, task="", events=None):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.results[min(self.calls - 1, len(self.results) - 1)]


async def _done(manager: Manager) -> str:
    return await asyncio.wait_for(manager.subscribe_done().get(), timeout=2)


async def test_task_list_running():
    manager = Manager()
    task_id = await manager.launch(FakeAgent(delay=10), Conversation(), "worker")
    tool = TaskListTool(manager)
    result = await tool.execute("{}")
    data = json.loads(result.content)
    assert data[0]["id"] == task_id
    assert data[0]["name"] == "worker"
    assert data[0]["status"] == "running"
    assert "tool_count" in data[0]
    await manager.stop(task_id)
    await _done(manager)


async def test_task_get_known_and_unknown():
    manager = Manager()
    task_id = await manager.launch(FakeAgent(["value"]), Conversation(), "w", "task")
    await _done(manager)

    result = await TaskGetTool(manager).execute(json.dumps({"task_id": task_id}))
    data = json.loads(result.content)
    assert data["id"] == task_id
    assert data["result"] == "value"
    assert data["status"] == "completed"
    assert "usage" in data and "start_time" in data

    missing = await TaskGetTool(manager).execute(json.dumps({"task_id": "nope"}))
    assert missing.is_error is True


async def test_task_stop():
    manager = Manager()
    task_id = await manager.launch(FakeAgent(delay=10), Conversation())
    result = await TaskStopTool(manager).execute(json.dumps({"task_id": task_id}))
    assert json.loads(result.content)["status"] == "cancellation_requested"
    await _done(manager)
    assert manager.get(task_id).status is Status.CANCELLED

    missing = await TaskStopTool(manager).execute(json.dumps({"task_id": "nope"}))
    assert missing.is_error is True


async def test_send_message_tool():
    manager = Manager()
    fake = FakeAgent(["one", "two"])
    await manager.launch(fake, Conversation(), "worker")
    await _done(manager)

    result = await SendMessageTool(manager).execute(
        json.dumps({"name": "worker", "message": "again"})
    )
    assert json.loads(result.content)["status"] == "resumed"
    await _done(manager)
    assert fake.calls == 2


async def test_send_message_tool_errors():
    manager = Manager()
    unknown = await SendMessageTool(manager).execute(json.dumps({"name": "ghost", "message": "hi"}))
    assert unknown.is_error is True
    empty = await SendMessageTool(manager).execute(json.dumps({"name": "", "message": ""}))
    assert empty.is_error is True


async def test_task_tools_are_system_tools():
    manager = Manager()
    for tool in (
        TaskListTool(manager),
        TaskGetTool(manager),
        TaskStopTool(manager),
        SendMessageTool(manager),
    ):
        assert tool.is_system is True
