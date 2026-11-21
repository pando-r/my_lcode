"""The /team command: list/info/delete/kill."""

from __future__ import annotations

from lcode.command.ui import UI

USAGE = "/team list | info <name> | delete <name> [--force] | kill <member>"


async def handle_team(ui: UI) -> None:
    accessor = ui.team_accessor()
    if accessor is None:
        ui.error("Team 功能未启用")
        return
    argv = ui.command_args().split()
    if not argv:
        ui.println(USAGE)
        return
    subcommand, rest = argv[0], argv[1:]
    try:
        if subcommand == "list":
            _list(ui, accessor)
        elif subcommand == "info":
            await _info(ui, accessor, rest)
        elif subcommand == "delete":
            await _delete(ui, accessor, rest)
        elif subcommand == "kill":
            await _kill(ui, accessor, rest)
        else:
            ui.error(f"未知 team 子命令: {subcommand}\n{USAGE}")
    except Exception as exc:  # noqa: BLE001
        ui.error(str(exc))


def _list(ui: UI, accessor) -> None:
    teams = accessor.list()
    if not teams:
        ui.println("当前没有 Team")
        return
    lines = [
        f"{team.name}  {team.backend}  {team.member_count} 成员  "
        f"[{team.active_count}/{team.member_count}] 活跃"
        for team in teams
    ]
    ui.println("\n".join(lines))


async def _info(ui: UI, accessor, rest: list[str]) -> None:
    if len(rest) != 1:
        ui.error(USAGE)
        return
    summary, members = await accessor.info(rest[0])
    lines = [
        f"Team: {summary.name}",
        f"Backend: {summary.backend}",
        f"Config: {summary.config_path}",
        "Members:",
    ]
    for member in members:
        lines.append(
            f"  {member.name}  {member.agent_id}  {member.backend}  "
            f"active={member.is_active}  tasks={member.task_count}  "
            f"{member.worktree_path}"
        )
    ui.println("\n".join(lines))


async def _delete(ui: UI, accessor, rest: list[str]) -> None:
    names = [item for item in rest if not item.startswith("--")]
    if len(names) != 1:
        ui.error(USAGE)
        return
    await accessor.delete(names[0], "--force" in rest)
    ui.println(f"Team 已删除: {names[0]}")


async def _kill(ui: UI, accessor, rest: list[str]) -> None:
    if len(rest) != 1:
        ui.error(USAGE)
        return
    team_name = await accessor.kill(rest[0])
    ui.println(f"已终止队员 {rest[0]}（Team {team_name}）")


__all__ = ["USAGE", "handle_team"]
