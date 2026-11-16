"""TUI glue for background task notifications."""

from __future__ import annotations

import asyncio

from mewcode.task import BackgroundTask


def build_task_notification(task: BackgroundTask) -> str:
    # F19：作为 system reminder 注入，不直接写 conversation，也不消耗工具配额。
    label = f' (name="{task.name}")' if task.name else ""
    if task.status.name == "FAILED":
        detail = f"Error: {task.err}"
    else:
        detail = f"Result: {task.result}"
    return (
        f"<task-notification>\nTask {task.id}{label}: {task.status}\n{detail}\n</task-notification>"
    )


async def consume_task_done(app) -> None:
    # 常驻协程：任务完成后把通知放进主对话的 pending reminder 队列。
    queue = app.task_mgr.subscribe_done()
    while True:
        task_id = await queue.get()
        task = app.task_mgr.get(task_id)
        if task is None:
            continue
        app.runtime.append_reminders([build_task_notification(task)])


def build_team_update_reminder(messages) -> str:
    lines = ["<team-update>"]
    for message in messages:
        content = message.content
        if len(content) > 8000:
            content = content[:8000] + "...[truncated]"
        lines.append(
            f"[team={message.team_name}] 来自 {message.from_} "
            f"(type={message.type}, ts={message.timestamp}):"
        )
        lines.append(content)
    lines.append("</team-update>")
    return "\n".join(lines)


async def consume_lead_mail(app) -> None:
    # 每秒轮询所有 Team 的 lead 邮箱；收到消息后唤醒空闲 Lead。
    while True:
        await asyncio.sleep(1.0)
        try:
            messages = await app.team_mgr.poll_lead_mailboxes()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            continue
        if not messages:
            continue
        app.runtime.append_reminders([build_team_update_reminder(messages)])
        if app.idle():
            await app.begin_autonomous_turn()


__all__ = [
    "build_task_notification",
    "build_team_update_reminder",
    "consume_lead_mail",
    "consume_task_done",
]
