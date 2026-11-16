"""The /worktree command: manual worktree management."""

from __future__ import annotations

from mewcode.command.ui import UI

USAGE = (
    "/worktree create <slug> | list | enter <slug> | "
    "exit [--remove] [--discard] | remove <slug> [--discard]"
)


async def handle_worktree(ui: UI) -> None:
    accessor = ui.worktree_accessor()
    if accessor is None:
        ui.error("Worktree 功能未启用（当前目录不是 Git 仓库）")
        return

    argv = ui.command_args().split()
    if not argv:
        ui.println(USAGE)
        return

    subcommand, rest = argv[0], argv[1:]
    try:
        if subcommand == "create":
            await _create(ui, accessor, rest)
        elif subcommand == "list":
            _list(ui, accessor)
        elif subcommand == "enter":
            await _enter(ui, accessor, rest)
        elif subcommand == "exit":
            await _exit(ui, accessor, rest)
        elif subcommand == "remove":
            await _remove(ui, accessor, rest)
        else:
            ui.error(f"未知 worktree 子命令: {subcommand}\n{USAGE}")
    except Exception as exc:  # noqa: BLE001 - command failures surface to the user
        ui.error(str(exc))


async def _create(ui: UI, accessor, rest: list[str]) -> None:
    if len(rest) != 1:
        ui.error(USAGE)
        return
    path, branch = await accessor.create(rest[0])
    ui.println(f"Worktree 已创建: {path} (分支 {branch})")


def _list(ui: UI, accessor) -> None:
    items = accessor.list()
    if not items:
        ui.println("当前没有 Worktree")
        return
    lines = []
    for item in items:
        flags = []
        if item.active:
            flags.append("[active]")
        if item.manual:
            flags.append("[manual]")
        suffix = " " + " ".join(flags) if flags else ""
        lines.append(f"{item.name}  {item.path}  {item.branch}{suffix}")
    ui.println("\n".join(lines))


async def _enter(ui: UI, accessor, rest: list[str]) -> None:
    if len(rest) != 1:
        ui.error(USAGE)
        return
    await accessor.enter(rest[0])
    ui.println(f"已进入 Worktree: {rest[0]}")


async def _exit(ui: UI, accessor, rest: list[str]) -> None:
    action = "remove" if "--remove" in rest else "keep"
    discard = "--discard" in rest
    removed = await accessor.exit(action, discard)
    if removed:
        ui.println("已退出并删除当前 Worktree")
    else:
        ui.println("已退出当前 Worktree（保留目录）")


async def _remove(ui: UI, accessor, rest: list[str]) -> None:
    names = [item for item in rest if not item.startswith("--")]
    if len(names) != 1:
        ui.error(USAGE)
        return
    await accessor.remove(names[0], "--discard" in rest)
    ui.println(f"已删除 Worktree: {names[0]}")


__all__ = ["USAGE", "handle_worktree"]
