from __future__ import annotations

import json
from pathlib import Path

from mewcode.config import ProviderConfig
from mewcode.llm import StreamEvent, ToolCall
from mewcode.permission import new_engine
from mewcode.tool import new_default_registry
from mewcode.tui.app import MewCodeApp
from mewcode.worktree import Manager


class ScriptedProvider:
    def __init__(self, scripts):
        self.scripts = scripts
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        self.calls += 1
        idx = min(self.calls - 1, len(self.scripts) - 1)
        for event in self.scripts[idx]:
            yield event


def _app(repo: Path, worktree_mgr=None) -> MewCodeApp:
    providers = [ProviderConfig(name="fake", protocol="openai", api_key="k", model="m")]
    return MewCodeApp(
        providers,
        "dev",
        new_default_registry(),
        new_engine(str(repo))[0],
        workspace=str(repo),
        context_window=128000,
        worktree_mgr=worktree_mgr,
    )


async def _wait(app, pilot, turns=1):
    for _ in range(200):
        if app.idle() and app.runtime.turn_count >= turns:
            return
        await pilot.pause()


async def test_worktree_adapter_create_enter_exit(git_repo):
    manager = Manager(str(git_repo))
    app = _app(git_repo, manager)
    async with app.run_test():
        accessor = app.worktree_accessor()
        assert accessor is not None
        path, branch = await accessor.create("alice")
        assert Path(path).is_dir()
        assert branch == "worktree-alice"

        items = accessor.list()
        assert [item.name for item in items] == ["alice"]
        assert items[0].manual is True
        assert items[0].active is False

        before = Path.cwd()
        await accessor.enter("alice")
        assert app.active_cwd == path
        assert Path.cwd() == before  # 进程 cwd 不被修改
        assert accessor.list()[0].active is True

        removed = await accessor.exit("keep", False)
        assert removed is False
        assert app.active_cwd == ""
        await app.action_quit()


async def test_app_restores_active_cwd_from_session(git_repo):
    manager = Manager(str(git_repo))
    worktree = await manager.create("alice", "HEAD", manual=True)
    await manager.enter("alice")
    app = _app(git_repo, manager)
    assert app.active_cwd == worktree.path
    async with app.run_test():
        assert app._effective_cwd() == worktree.path
        await app.action_quit()


async def test_main_agent_tool_calls_use_active_cwd(git_repo):
    manager = Manager(str(git_repo))
    app = _app(git_repo, manager)
    async with app.run_test() as pilot:
        app.active_cwd = str(git_repo)
        app.provider = ScriptedProvider(
            [
                [
                    StreamEvent(
                        tool_calls=[
                            ToolCall(
                                id="c1",
                                name="read_file",
                                input=json.dumps({"path": "a.txt"}),
                            )
                        ]
                    ),
                    StreamEvent(done=True),
                ],
                [StreamEvent(text="done"), StreamEvent(done=True)],
            ]
        )
        await app.submit("read a.txt")
        await _wait(app, pilot)

        tool_messages = [m for m in app.conv.messages() if m.role == "tool"]
        assert "hello" in tool_messages[0].tool_results[0].content
        await app.action_quit()
