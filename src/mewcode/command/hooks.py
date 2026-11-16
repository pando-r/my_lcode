"""The /hooks built-in command: list loaded rules grouped by event."""

from __future__ import annotations

from mewcode.command.ui import UI, HookSummary


async def handle_hooks(ui: UI) -> None:
    summaries = ui.hook_summaries()
    if not summaries:
        ui.println("当前没有已加载的 Hook")
        return

    # 按事件首次出现顺序分组，保持配置文件中的稳定顺序。
    grouped: dict[str, list[HookSummary]] = {}
    for summary in summaries:
        grouped.setdefault(summary.event, []).append(summary)

    lines = ["Hooks:"]
    for event, items in grouped.items():
        lines.append(f"  {event}:")
        for item in items:
            flags = ""
            if item.only_once:
                flags += " [once]"
            if item.async_mode:
                flags += " [async]"
            lines.append(f"    - {item.name}{flags} ({item.action_type})")
    sources = ui.hook_sources()
    if sources:
        lines.append("Loaded from:")
        lines.extend(f"  - {source}" for source in sources)
    ui.println("\n".join(lines))
