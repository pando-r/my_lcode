from __future__ import annotations

from mewcode.command.builtin_worktree import handle_worktree
from mewcode.command.ui import NopUI, WorktreeSummary


class StubAccessor:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.entered: list[str] = []
        self.exited: list[tuple[str, bool]] = []
        self.removed: list[tuple[str, bool]] = []
        self.items = [
            WorktreeSummary(
                name="alice",
                path="/repo/.mewcode/worktrees/alice",
                branch="worktree-alice",
                active=True,
                manual=True,
            )
        ]

    async def create(self, name: str) -> tuple[str, str]:
        self.created.append(name)
        return f"/repo/.mewcode/worktrees/{name}", f"worktree-{name}"

    def list(self) -> list[WorktreeSummary]:
        return list(self.items)

    async def enter(self, name: str) -> None:
        self.entered.append(name)

    async def exit(self, action: str, discard: bool) -> bool:
        self.exited.append((action, discard))
        return action == "remove"

    async def remove(self, name: str, discard: bool) -> None:
        self.removed.append((name, discard))


class WorktreeUI(NopUI):
    def __init__(self, accessor) -> None:
        super().__init__()
        self._accessor = accessor

    def worktree_accessor(self):
        return self._accessor


async def _run(args: str, accessor):
    ui = WorktreeUI(accessor)
    ui.set_command_args(args)
    await handle_worktree(ui)
    return ui


async def test_create():
    accessor = StubAccessor()
    ui = await _run("create alice", accessor)
    assert accessor.created == ["alice"]
    assert "Worktree 已创建:" in ui.printed[-1]
    assert "worktree-alice" in ui.printed[-1]


async def test_list_with_flags():
    accessor = StubAccessor()
    ui = await _run("list", accessor)
    text = ui.printed[-1]
    assert "alice" in text
    assert "worktree-alice" in text
    assert "[active]" in text
    assert "[manual]" in text


async def test_list_empty():
    accessor = StubAccessor()
    accessor.items = []
    ui = await _run("list", accessor)
    assert ui.printed[-1] == "当前没有 Worktree"


async def test_enter():
    accessor = StubAccessor()
    ui = await _run("enter alice", accessor)
    assert accessor.entered == ["alice"]
    assert "已进入 Worktree: alice" in ui.printed[-1]


async def test_exit_remove_with_discard():
    accessor = StubAccessor()
    ui = await _run("exit --remove --discard", accessor)
    assert accessor.exited == [("remove", True)]
    assert "已退出并删除" in ui.printed[-1]


async def test_exit_keep():
    accessor = StubAccessor()
    ui = await _run("exit", accessor)
    assert accessor.exited == [("keep", False)]
    assert "保留目录" in ui.printed[-1]


async def test_remove():
    accessor = StubAccessor()
    ui = await _run("remove alice --discard", accessor)
    assert accessor.removed == [("alice", True)]
    assert "已删除 Worktree: alice" in ui.printed[-1]


async def test_unknown_subcommand_and_no_args():
    accessor = StubAccessor()
    ui = await _run("bogus", accessor)
    assert any("未知 worktree 子命令" in msg for msg in ui.errors)

    ui = await _run("", accessor)
    assert "create" in ui.printed[-1]


async def test_no_accessor_reports_error():
    ui = WorktreeUI(None)
    ui.set_command_args("list")
    await handle_worktree(ui)
    assert any("Worktree 功能未启用" in msg for msg in ui.errors)


async def test_accessor_exception_is_reported():
    class Broken(StubAccessor):
        async def create(self, name: str):
            raise ValueError("bad slug")

    ui = await _run("create ..", Broken())
    assert any("bad slug" in msg for msg in ui.errors)
