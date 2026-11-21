from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from lcode.team.filelock import LOCK_STALE_AFTER, acquire
from lcode.team.mailbox import Box, Message, MessageType


async def test_roundtrip(tmp_path):
    box = Box(str(tmp_path / "mailbox"))
    message = Message(from_="lead", text="hello", type=MessageType.TEXT)
    await box.write("agent-1", message)

    messages = await box.read("agent-1")
    assert len(messages) == 1
    assert messages[0].from_ == "lead"
    assert messages[0].text == "hello"
    assert messages[0].read is False
    assert messages[0].timestamp


async def test_structured_message_fields(tmp_path):
    box = Box(str(tmp_path / "mailbox"))
    await box.write(
        "agent-1",
        Message(
            from_="lead",
            text="ok",
            type=MessageType.PLAN_APPROVAL_RESPONSE,
            request_id="req-1",
            approve=True,
        ),
    )
    raw = (tmp_path / "mailbox" / "agent-1.json").read_text(encoding="utf-8")
    assert '"from": "lead"' in raw
    assert '"requestId": "req-1"' in raw
    assert '"approve": true' in raw
    msg = (await box.read("agent-1"))[0]
    assert msg.request_id == "req-1" and msg.approve is True


async def test_mark_read(tmp_path):
    box = Box(str(tmp_path / "mailbox"))
    await box.write("a", Message(from_="lead", text="one"))
    await box.write("a", Message(from_="lead", text="two"))
    indices, messages = await box.read_unread("a")
    assert indices == [0, 1] and len(messages) == 2
    await box.mark_read("a", [0])
    indices, messages = await box.read_unread("a")
    assert indices == [1] and messages[0].text == "two"


async def test_concurrent_writes_no_loss(tmp_path):
    box = Box(str(tmp_path / "mailbox"))

    async def writer(index: int) -> None:
        await box.write("a", Message(from_="lead", text=f"m{index}"))

    await asyncio.gather(*(writer(i) for i in range(10)))
    messages = await box.read("a")
    assert len(messages) == 10
    assert {message.text for message in messages} == {f"m{i}" for i in range(10)}


async def test_stale_lock_is_stolen(tmp_path):
    lock_path = tmp_path / "x.lock"
    lock_path.write_text("", encoding="utf-8")
    old = time.time() - LOCK_STALE_AFTER - 5
    os.utime(lock_path, (old, old))
    async with acquire(str(lock_path)):
        assert lock_path.exists()
    assert not lock_path.exists()


async def test_lock_serializes(tmp_path):
    lock_path = str(Path(tmp_path) / "x.lock")
    order: list[str] = []

    async def worker(name: str) -> None:
        async with acquire(lock_path):
            order.append(name)
            await asyncio.sleep(0.01)

    await asyncio.gather(worker("a"), worker("b"), worker("c"))
    assert sorted(order) == ["a", "b", "c"]
    assert not Path(lock_path).exists()
