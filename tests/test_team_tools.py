from __future__ import annotations

import json

from lcode.team import BackendType, Manager, TeammateInfo
from lcode.team.mailbox import Box
from lcode.team.tools import (
    TaskCreateTool,
    TaskUpdateTool,
    TeamAwareSendMessageTool,
    TeamAwareTaskGetTool,
    TeamAwareTaskListTool,
    TeamCreateTool,
    TeamDeleteTool,
)


def _manager(tmp_path, git_repo) -> Manager:
    return Manager(tmp_path / "home", git_repo)


async def _team(manager: Manager):
    result = await TeamCreateTool(manager).execute(
        json.dumps({"team_name": "demo", "description": "d"})
    )
    assert not result.is_error
    return manager.get("demo")


def test_team_tools_registration(tmp_path, git_repo):
    from lcode.task import (
        Manager as TaskManager,
    )
    from lcode.task import (
        SendMessageTool,
        TaskGetTool,
        TaskListTool,
    )
    from lcode.team.tools import team_tools
    from lcode.tool import Registry

    manager = _manager(tmp_path, git_repo)
    task_mgr = TaskManager()
    tools = team_tools(
        manager,
        TaskListTool(task_mgr),
        TaskGetTool(task_mgr),
        SendMessageTool(task_mgr),
    )
    registry = Registry()
    for tool in tools:
        registry.register(tool)
    assert {tool.name() for tool in tools} == {
        "TeamCreate",
        "TeamDelete",
        "TaskCreate",
        "TaskGet",
        "TaskList",
        "TaskUpdate",
        "SendMessage",
    }


async def test_team_create_delete(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    team = await _team(manager)
    payload = json.loads(
        (await TeamCreateTool(manager).execute(json.dumps({"team_name": "x"}))).content
    )
    assert payload["team_name"] == "x"
    assert payload["backend"] in {"tmux", "iterm2", "in-process"}

    await team.set_member_active("lead", False)
    result = await TeamDeleteTool(manager).execute(json.dumps({"team_name": "demo", "force": True}))
    assert not result.is_error
    assert manager.get("demo") is None


async def test_task_tools(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    await _team(manager)

    created = await TaskCreateTool(manager).execute(
        json.dumps({"title": "first", "assignee": "alice"})
    )
    task_id = json.loads(created.content)["task_id"]

    detail = await TeamAwareTaskGetTool(manager, None).execute(json.dumps({"task_id": task_id}))
    assert json.loads(detail.content)["title"] == "first"

    second = await TaskCreateTool(manager).execute(json.dumps({"title": "second"}))
    second_id = json.loads(second.content)["task_id"]
    updated = await TaskUpdateTool(manager).execute(
        json.dumps({"task_id": task_id, "add_blocks": [second_id]})
    )
    assert second_id in json.loads(updated.content)["blocks"]

    listed = await TeamAwareTaskListTool(manager, None).execute("{}")
    tasks = json.loads(listed.content)
    assert {task["title"] for task in tasks} == {"first", "second"}
    assert any(task["is_ready"] is False for task in tasks)


async def test_send_message_to_member_and_broadcast(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    team = await _team(manager)
    await team.add_member(
        TeammateInfo(
            name="alice",
            agent_id="agent-a1",
            backend_type=BackendType.IN_PROCESS,
            is_active=True,
        )
    )
    await team.add_member(
        TeammateInfo(
            name="bob",
            agent_id="agent-b1",
            backend_type=BackendType.IN_PROCESS,
            is_active=True,
        )
    )
    manager.registry.register("alice", "agent-a1")
    manager.registry.register("bob", "agent-b1")

    tool = TeamAwareSendMessageTool(manager, None)
    result = await tool.execute(json.dumps({"to": "alice", "content": "hello"}))
    assert not result.is_error
    messages = await Box(team.mailbox_dir).read("agent-a1")
    assert messages[0].text == "hello"

    await tool.execute(json.dumps({"to": "*", "content": "all"}))
    alice_messages = await Box(team.mailbox_dir).read("agent-a1")
    bob_messages = await Box(team.mailbox_dir).read("agent-b1")
    assert any(m.text == "all" for m in alice_messages)
    assert any(m.text == "all" for m in bob_messages)


async def test_send_message_plan_approval_requires_lead(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    team = await _team(manager)
    await team.add_member(TeammateInfo(name="alice", agent_id="a1", is_active=True))
    manager.registry.register("alice", "a1")

    from lcode.agent.team_hook import TeammateContext, with_teammate_context

    teammate = TeammateContext(team_name=team.sanitized_name, member_name="alice", agent_id="a1")
    tool = TeamAwareSendMessageTool(manager, None)
    with with_teammate_context(teammate):
        result = await tool.execute(
            json.dumps(
                {
                    "to": "lead",
                    "type": "plan_approval_response",
                    "request_id": "r1",
                    "approve": True,
                    "content": "ok",
                }
            )
        )
    assert result.is_error
    assert "Lead" in result.content


async def test_unknown_target(tmp_path, git_repo):
    manager = _manager(tmp_path, git_repo)
    await _team(manager)
    result = await TeamAwareSendMessageTool(manager, None).execute(
        json.dumps({"to": "ghost", "content": "hi"})
    )
    assert result.is_error
