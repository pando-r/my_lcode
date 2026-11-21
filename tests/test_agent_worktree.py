from __future__ import annotations

import json
from pathlib import Path

from lcode.agent import Agent
from lcode.agent.agent_tool import AgentTool
from lcode.agent.agent_worktree import _execute_with_worktree, build_worktree_notice
from lcode.agent.tool_context import ToolCallContext, bind_tool_context
from lcode.conversation import Conversation
from lcode.llm import StreamEvent
from lcode.permission import new_engine
from lcode.subagent import load_catalog
from lcode.task import Manager as TaskManager
from lcode.tool import Registry, Result
from lcode.tool.ctx import cwd_from_ctx, resolve_path
from lcode.worktree import ExitOptions, Manager


class FakeReadTool:
    read_only = True

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "read"

    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, args: str) -> Result:
        return Result(content="ok")


class StubSubAgent:
    def __init__(self, write: bool = False) -> None:
        self.write = write
        self.seen = {}

    async def run_to_completion(self, conv, task="", events=None):
        self.seen["cwd"] = cwd_from_ctx()
        self.seen["task"] = task
        if self.write:
            Path(resolve_path("inside.txt")).write_text("x", encoding="utf-8")
        return "done"


class ScriptedProvider:
    def __init__(self, text="result"):
        self.text = text

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        yield StreamEvent(text=self.text)
        yield StreamEvent(done=True)


def test_build_worktree_notice():
    notice = build_worktree_notice("/parent", "/parent/.lcode/worktrees/a")
    assert notice.startswith("<worktree-context>")
    assert "/parent" in notice
    assert "/parent/.lcode/worktrees/a" in notice
    assert notice.rstrip().endswith("</worktree-context>")


async def test_execute_with_worktree_passes_cwd_and_cleans_up(git_repo):
    manager = Manager(str(git_repo))
    stub = StubSubAgent(write=False)
    result = await _execute_with_worktree(manager, None, stub, Conversation(), "do work", None)
    assert result == "done"
    assert stub.seen["cwd"] is not None
    assert str(Path(manager.worktree_dir)) in stub.seen["cwd"]
    assert stub.seen["task"].startswith("<worktree-context>")
    assert "do work" in stub.seen["task"]
    # 无变更 → auto_cleanup 删除，目录不留。
    assert not Path(stub.seen["cwd"]).exists()


async def test_execute_with_worktree_keeps_changed(git_repo):
    manager = Manager(str(git_repo))
    stub = StubSubAgent(write=True)
    result = await _execute_with_worktree(manager, None, stub, Conversation(), "do work", None)
    assert result.startswith("done")
    assert "[Worktree 保留在" in result
    worktree_path = Path(stub.seen["cwd"])
    assert (worktree_path / "inside.txt").read_text(encoding="utf-8") == "x"
    assert not (Path(git_repo) / "inside.txt").exists()
    await manager.remove(manager.list()[0].name, ExitOptions(discard_changes=True))


def _write_agent(tmp_path, name="iso", isolation="worktree"):
    path = tmp_path / ".lcode" / "agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: d\nisolation: {isolation}\n---\nbody",
        encoding="utf-8",
    )


async def test_agent_tool_isolation_forces_foreground(git_repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_agent(git_repo)
    catalog = load_catalog(str(git_repo))
    worktree_manager = Manager(str(git_repo))
    task_manager = TaskManager()
    registry = Registry()
    registry.register(FakeReadTool())
    parent = Agent(
        ScriptedProvider("isolated done"),
        registry,
        "dev",
        new_engine(str(git_repo))[0],
    )
    tool = AgentTool(catalog, task_manager, parent=parent, worktree_mgr=worktree_manager)
    conv = Conversation()
    conv.add_user("parent")
    with bind_tool_context(ToolCallContext(parent, conv)):
        result = await tool.execute(
            json.dumps(
                {
                    "prompt": "work",
                    "description": "d",
                    "subagent_type": "iso",
                    "run_in_background": True,
                }
            )
        )
    assert result.is_error is False
    assert result.content == "isolated done"
    assert task_manager.list() == []
    assert worktree_manager.list() == []


async def test_agent_tool_isolation_without_manager(git_repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    _write_agent(git_repo)
    catalog = load_catalog(str(git_repo))
    registry = Registry()
    registry.register(FakeReadTool())
    parent = Agent(ScriptedProvider(), registry, "dev", new_engine(str(git_repo))[0])
    tool = AgentTool(catalog, TaskManager(), parent=parent, worktree_mgr=None)
    conv = Conversation()
    conv.add_user("parent")
    with bind_tool_context(ToolCallContext(parent, conv)):
        result = await tool.execute(
            json.dumps({"prompt": "work", "description": "d", "subagent_type": "iso"})
        )
    assert result.is_error is True
    assert "worktree manager not configured" in result.content
