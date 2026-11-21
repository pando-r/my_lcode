"""Pure-local commands: /help /status /memory /permission /session."""

from __future__ import annotations

from lcode.command.command import Handler
from lcode.command.registry import Registry
from lcode.command.ui import UI

STATUS_KEYS = ("Mode:", "Tokens:", "Tools:", "Memories:", "Model:", "Directory:")


def make_help_handler(reg: Registry) -> Handler:
    async def _handler(ui: UI) -> None:
        commands = reg.visible()
        width = max((len(c.name) for c in commands), default=0)
        lines = [f"/{c.name.ljust(width)}  {c.description}" for c in commands]
        ui.println("\n".join(lines))

    return _handler


async def handle_status(ui: UI) -> None:
    width = max(len(key) for key in STATUS_KEYS)
    values = (
        str(ui.mode()),
        f"{ui.usage_in()} in / {ui.usage_out()} out",
        f"{ui.tool_count()} enabled",
        f"{len(ui.memory_files())} files",
        ui.model_name(),
        ui.cwd(),
    )
    lines = ["Lcode Status", ""]
    lines += [f"{key.ljust(width)} {value}" for key, value in zip(STATUS_KEYS, values)]
    ui.println("\n".join(lines))


async def handle_memory(ui: UI) -> None:
    files = ui.memory_files()
    if not files:
        ui.println("无已加载的记忆文件")
        return
    ui.println("\n".join(files))


async def handle_permission(ui: UI) -> None:
    ui.println(str(ui.mode()))


async def handle_session(ui: UI) -> None:
    ui.println(f"Session: {ui.session_id()}\nPath: {ui.session_path()}")
