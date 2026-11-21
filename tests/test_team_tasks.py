from __future__ import annotations

import json
import re

from lcode.team.tasks import Filter, Patch, Status, Store, Task


async def test_create_get(tmp_path):
    store = Store(str(tmp_path / "tasks.json"))
    task_id = await store.create(Task(id="", title="do it"))
    assert re.match(r"^task_[0-9a-f]{6}$", task_id)
    task = await store.get(task_id)
    assert task.title == "do it"
    assert task.status is Status.PENDING
    data = json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))
    assert data["tasks"][0]["id"] == task_id


async def test_update_and_bidirectional_dependencies(tmp_path):
    store = Store(str(tmp_path / "tasks.json"))
    first = await store.create(Task(id="", title="first"))
    second = await store.create(Task(id="", title="second"))

    await store.update(first, Patch(add_blocks=[second]))
    a = await store.get(first)
    b = await store.get(second)
    assert second in a.blocks
    assert first in b.blocked_by

    await store.update(second, Patch(status=Status.COMPLETED))
    await store.update(first, Patch(remove_blocks=[second]))
    a = await store.get(first)
    b = await store.get(second)
    assert second not in a.blocks
    assert first not in b.blocked_by


async def test_add_blocked_by_direction(tmp_path):
    store = Store(str(tmp_path / "tasks.json"))
    first = await store.create(Task(id="", title="first"))
    second = await store.create(Task(id="", title="second"))
    await store.update(second, Patch(add_blocked_by=[first]))
    a = await store.get(first)
    b = await store.get(second)
    assert second in a.blocks
    assert first in b.blocked_by


async def test_list_is_ready_and_filter(tmp_path):
    store = Store(str(tmp_path / "tasks.json"))
    blocker = await store.create(Task(id="", title="blocker"))
    blocked = await store.create(Task(id="", title="blocked", blocked_by=[blocker]))
    free = await store.create(Task(id="", title="free"))

    tasks = await store.list_(Filter())
    by_id = {task.id: task for task in tasks}
    assert by_id[blocked].is_ready is False
    assert by_id[free].is_ready is True

    await store.update(blocker, Patch(status=Status.COMPLETED))
    tasks = await store.list_(Filter(status=Status.PENDING))
    by_id = {task.id: task for task in tasks}
    assert by_id[blocked].is_ready is True
    assert blocker not in by_id


async def test_get_unknown(tmp_path):
    store = Store(str(tmp_path / "tasks.json"))
    try:
        await store.get("task_missing")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")
