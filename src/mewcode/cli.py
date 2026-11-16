"""Entry point: load config, connect MCP servers, then launch the TUI."""

# 按固定顺序把散落的部件——配置、工具注册表、MCP 连接、权限引擎——组装成一个可运行的 TUI 应用。
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from mewcode import __version__
from mewcode import instructions as instructions_pkg
from mewcode import memory as memory_pkg
from mewcode import session as session_pkg
from mewcode import skills as skills_pkg
from mewcode.compact import new_session_context
from mewcode.config import ConfigError, load
from mewcode.permission import new_engine
from mewcode.tool import new_default_registry


async def _amain(member_args=None) -> int:
    if member_args is not None and getattr(member_args, "worktree", ""):
        os.chdir(member_args.worktree)
    try:
        cfg = load(".mewcode/config.yaml")
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Imported lazily so a config error is reported before pulling in the TUI stack.
    from mewcode import mcp as mcp_client
    from mewcode.hook import load as load_hooks
    from mewcode.tui.app import MewCodeApp

    registry = new_default_registry()
    root = str(Path.cwd().resolve())

    mcp_cfg = mcp_client.load_config(root)
    mcp_mgr = await mcp_client.new_manager(mcp_cfg, __version__)

    # 项目指令 + 长期记忆索引（启动时加载一次）。
    instruction_text = instructions_pkg.Loader(root).load()
    mem_mgr = memory_pkg.Manager(
        project_dir=str(Path(root) / ".mewcode" / "memory"),
        user_dir=str(Path.home() / ".mewcode" / "memory"),
    )
    memory_text = mem_mgr.load_index()

    # Skill catalog：启动期扫描项目级与用户级目录。
    from mewcode import coordinator
    from mewcode.agent.agent_tool import AgentTool
    from mewcode.subagent import load_catalog
    from mewcode.task import Manager, SendMessageTool, TaskGetTool, TaskListTool, TaskStopTool
    from mewcode.team import AgentNameRegistry
    from mewcode.team import Manager as TeamManager
    from mewcode.team.tools import team_tools
    from mewcode.tool.install_skill import InstallSkillTool
    from mewcode.tool.load_skill import LoadSkillTool

    skill_loader = skills_pkg.SkillLoader(root)
    skill_loader.load_all()
    load_skill_tool = LoadSkillTool()
    install_skill_tool = InstallSkillTool()

    # SubAgent：三层角色定义 + 后台任务管理器 + 5 个内置工具。
    subagent_catalog = load_catalog(root)
    task_mgr = Manager()
    name_reg = AgentNameRegistry()
    task_mgr.set_name_registry(name_reg)
    coordinator_mode = coordinator.is_enabled(cfg)

    # Worktree：非 Git 仓库/异常时降级为 None，不阻断启动。
    from mewcode import worktree as worktree_pkg

    try:
        worktree_mgr = worktree_pkg.Manager(root)
    except Exception as exc:  # noqa: BLE001 - worktree is optional
        print(f"Worktree 管理器降级: {exc}", file=sys.stderr)
        worktree_mgr = None
    if worktree_mgr is not None:
        _warn_missing_worktree_ignores(root)

    team_mgr = TeamManager(
        home_dir=Path.home(),
        project_root=root,
        wt_mgr=worktree_mgr,
        task_mgr=task_mgr,
        name_reg=name_reg,
        catalog=subagent_catalog,
        fork_teammate_enabled=cfg.features.fork_teammate,
    )

    async def _on_task_done(task_id: str) -> None:
        await team_mgr.handle_task_done(task_id)

    task_mgr.on_task_done(_on_task_done)

    agent_tool = AgentTool(
        subagent_catalog,
        task_mgr,
        parent=None,
        bg_enabled=cfg.effective_enable_subagent_background(),
        worktree_mgr=worktree_mgr,
        team_hook=team_mgr,
    )

    # Hook：项目级 + 用户级 YAML；任何加载错误只报 stderr，不影响启动（N1）。
    try:
        hook_engine = load_hooks(root)
    except Exception as exc:  # noqa: BLE001 - hooks must never block startup
        print(f"hooks 加载失败: {exc}", file=sys.stderr)
        hook_engine = None

    # 新会话目录 + JSONL 写入器。
    sessions_dir = str(Path(root) / ".mewcode" / "sessions")
    ses_ctx = new_session_context(root)
    writer = session_pkg.Writer(ses_ctx.session_dir)

    # 过期会话清理在后台执行，不阻塞启动（F26）。
    cleanup_task = asyncio.create_task(
        asyncio.to_thread(session_pkg.clean_expired, sessions_dir, timedelta(days=30))
    )
    sweep_task = (
        asyncio.create_task(worktree_mgr.sweep_stale(datetime.now() - timedelta(hours=24)))
        if worktree_mgr is not None
        else None
    )

    try:
        for tool in mcp_mgr.tools():
            registry.register(tool)
        for tool in (
            load_skill_tool,
            install_skill_tool,
            TaskStopTool(task_mgr),
            *team_tools(
                team_mgr,
                TaskListTool(task_mgr),
                TaskGetTool(task_mgr),
                SendMessageTool(task_mgr),
            ),
            agent_tool,
        ):
            if registry.get(tool.name()) is None:
                registry.register(tool)

        engine, err = new_engine(root)
        if err is not None:
            print("权限引擎降级:", err, file=sys.stderr)

        if member_args is not None and getattr(member_args, "team_member", False):
            from mewcode.team.member import run_team_member

            await run_team_member(
                member_args,
                team_mgr=team_mgr,
                catalog=subagent_catalog,
                registry=registry,
                engine=engine,
                hook_engine=hook_engine,
                cfg=cfg,
            )
            return 0

        app = MewCodeApp(
            cfg.providers,
            __version__,
            registry,
            engine,
            writer=writer,
            mem_mgr=mem_mgr,
            instruction_text=instruction_text,
            memory_text=memory_text,
            sessions_dir=sessions_dir,
            workspace=root,
            ses_ctx=ses_ctx,
            skill_loader=skill_loader,
            load_skill_tool=load_skill_tool,
            install_skill_tool=install_skill_tool,
            hook_engine=hook_engine,
            task_mgr=task_mgr,
            subagent_catalog=subagent_catalog,
            worktree_mgr=worktree_mgr,
            team_mgr=team_mgr,
            coordinator_mode=coordinator_mode,
        )
        try:
            try:
                await app.run_async()
            except Exception as exc:  # noqa: BLE001 - report startup/runtime errors cleanly
                print(f"mewcode 启动失败: {exc}", file=sys.stderr)
                return 1
        finally:
            # TUI 正常退出时 action_quit 已派发；这里兜底异常/强制退出路径。
            await app.ensure_session_end("exit")
    finally:
        writer.close()
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        if sweep_task is not None:
            sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task
        await mcp_mgr.close()
        if hook_engine is not None:
            await hook_engine.aclose()
    return 0


def _warn_missing_worktree_ignores(root: str) -> None:
    # F35：只警告，不擅自修改用户的 .gitignore。
    path = Path(root) / ".gitignore"
    if not path.is_file():
        return
    lines = set(path.read_text(encoding="utf-8", errors="replace").splitlines())
    missing = [
        entry
        for entry in (".mewcode/worktrees/", ".mewcode/worktree_session.json")
        if entry not in lines
    ]
    if missing:
        print(
            "Worktree 提示: .gitignore 缺少 " + "、".join(missing),
            file=sys.stderr,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mewcode", description="MewCode 终端 Agent")
    parser.add_argument("--team-member", action="store_true", help="以 Team 队员模式运行")
    parser.add_argument("--team", default="", help="Team 名")
    parser.add_argument("--member", default="", help="队员名")
    parser.add_argument("--agent-id", default="", help="队员 agent_id")
    parser.add_argument("--session-dir", default="", help="队员 session 目录")
    parser.add_argument("--worktree", default="", help="队员 worktree 目录")
    parser.add_argument("--agent-type", default="", help="Agent 角色名")
    parser.add_argument("--model", default="", help="模型覆盖")
    parser.add_argument("--plan-mode", action="store_true", help="以 plan 模式启动")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(_amain(args)))
    except KeyboardInterrupt:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
