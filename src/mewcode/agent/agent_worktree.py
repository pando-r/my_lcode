"""Run a sub-agent in an isolated Git worktree."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mewcode.tool.ctx import cwd_from_ctx, with_cwd
from mewcode.worktree import Manager, random_agent_name
from mewcode.worktree.git import warn

if TYPE_CHECKING:
    from mewcode.subagent import Definition


def build_worktree_notice(parent_cwd: str, wt_path: str) -> str:
    return (
        "<worktree-context>\n"
        "你当前在一个独立的 Git Worktree 副本中工作，与父 Agent 隔离。\n"
        f"- 父目录: {parent_cwd}\n"
        f"- 你的工作目录: {wt_path}\n"
        "- 父 Agent 提到的绝对路径基于父目录，你需要翻译成本地路径（替换前缀）再读写\n"
        "- 编辑文件前，必须先在本地 Worktree 重新 read_file 一次，避免使用过时内容\n"
        "</worktree-context>"
    )


async def _execute_with_worktree(
    manager: Manager,
    definition: Definition,
    sub_agent: Any,
    sub_conv: Any,
    prompt: str,
    events: asyncio.Queue | None = None,
) -> str:
    name = random_agent_name()
    worktree = await manager.create(name, "HEAD", manual=False)
    final_text = ""
    report = None
    try:
        parent_cwd = cwd_from_ctx() or str(Path.cwd())
        notice = build_worktree_notice(parent_cwd, worktree.path)
        task_text = notice + "\n\n" + prompt
        with with_cwd(worktree.path):
            final_text = await sub_agent.run_to_completion(sub_conv, task_text, events)
    finally:
        try:
            report = await manager.auto_cleanup(name)
        except Exception as exc:  # noqa: BLE001 - cleanup failure must not mask result
            warn(f"auto_cleanup {name}: {exc}")

    if report is not None and report.kept:
        final_text += f"\n[Worktree 保留在 {report.path}，分支 {report.branch}]"
    return final_text


__all__ = ["_execute_with_worktree", "build_worktree_notice"]
