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

from lcode import __version__
from lcode import config as config_mod
from lcode import instructions as instructions_pkg
from lcode import memory as memory_pkg
from lcode import session as session_pkg
from lcode import skills as skills_pkg
from lcode.compact import new_session_context
from lcode.config import ConfigError, load
from lcode.permission import new_engine
from lcode.tool import new_default_registry

# 配置准备结果
STATUS_READY = "ready"
STATUS_INIT = "init"
STATUS_NEEDS_KEY = "needs_key"


def _prepare_config(explicit: str | None, init_only: bool) -> tuple[Path | None, str]:
    """决定本次运行用哪份配置；缺失时生成用户级模板。

    优先级：--config > LCODE_CONFIG > ./.lcode/config.yaml > ~/.lcode/config.yaml。
    都找不到时写一份模板到 ~/.lcode/config.yaml，让「装完直接敲 lcode」也能得到明确指引。
    """
    from lcode import bootstrap

    if explicit:
        return Path(explicit).expanduser(), STATUS_READY

    found = config_mod.resolve()
    if found is not None:
        return found, STATUS_READY

    path, created = bootstrap.ensure_user_config()
    if created:
        print(bootstrap.banner(path), file=sys.stderr)
    if init_only:
        print(f"配置模板已就绪: {path}")
        return None, STATUS_INIT
    return path, STATUS_NEEDS_KEY


def _resident_dir(root: str) -> Path:
    return Path(root) / ".lcode" / "resident"


async def _resident_early_exit(args) -> int | None:
    """处理 --enqueue / --tasks 这类一次性命令；不是它们就返回 None 继续正常流程。"""
    from lcode.resident.queue import TaskQueue

    root = str(Path.cwd().resolve())

    text = getattr(args, "enqueue", "") or ""
    if text.strip():
        task = TaskQueue(_resident_dir(root)).enqueue(text)
        print(f"已入队 {task.id}: {task.prompt.strip()[:80]}")
        return 0

    if getattr(args, "resident_tasks", False):
        queue = TaskQueue(_resident_dir(root))
        tasks = queue.load()
        if not tasks:
            print(f"队列为空（{queue.path}）")
            return 0
        print(f"{'ID':<14} {'状态':<8} {'创建时间':<20} 任务")
        for task in tasks:
            print(
                f"{task.id:<14} {task.status:<8} {task.created:<20} "
                f"{' '.join(task.prompt.split())[:60]}"
            )
            if task.result_file:
                print(f"{'':<14} {'':<8} → {task.result_file}")
            if task.error:
                print(f"{'':<14} {'':<8} ! {task.error}")
        return 0

    return None


async def _check_mail(root: str) -> int:
    """--check-mail：拉一次收件箱并入队，然后退出。

    刻意不执行任务：拉信和执行分开，出错时能立刻分清是「连不上邮箱」
    还是「模型跑挂了」，也让任务计划程序可以每 5 分钟只做一次廉价检查。
    """
    from lcode.mailbox import REPORTED_NAME, RejectionLog, fetch_new, load_config
    from lcode.resident.queue import ORIGIN_MAIL, TaskQueue

    cfg = load_config(root)
    if not cfg.ready():
        print(
            "收件箱未启用或配置不全：需要 .lcode/mail.yaml 里的 imap_host / "
            "senders / user / auth_code（auth 可继承 notify.yaml）",
            file=sys.stderr,
        )
        return 2

    queue = TaskQueue(_resident_dir(root))
    known = {task.message_id for task in queue.load() if task.message_id}
    result = fetch_new(cfg, skip_ids=known)

    # 退信留在收件箱里（不改用户的已读状态），每轮都会重新看到；只报第一次。
    log = RejectionLog(_resident_dir(root) / REPORTED_NAME)
    for item in log.take_new(result.rejected):
        print(f"退回：{item.sender} / {item.subject[:60]}（{item.reason}）")

    added: list[str] = []
    for mail in result.accepted:
        if mail.message_id and queue.has_message(mail.message_id):
            continue
        try:
            task = queue.enqueue(
                mail.prompt(),
                origin=ORIGIN_MAIL,
                reply_to=mail.sender,
                subject=mail.subject,
                message_id=mail.message_id,
            )
        except ValueError as exc:
            print(f"转任务失败：{exc}", file=sys.stderr)
            continue
        added.append(f"{task.id}  {' '.join(mail.prompt().split())[:60]}")

    if not added:
        print("没有新的可执行邮件")
        return 0
    print("\n".join([f"已入队 {len(added)} 条：", *added]))
    if not cfg.allow_write:
        print("（邮件任务以只读模式执行；需要写权限请加 --mail-allow-write 或改 mail.yaml）")
    return 0


async def _run_resident(cfg, args) -> int:
    """--resident / --resident-once：常驻循环或单轮执行。"""
    from lcode.permission import parse_mode
    from lcode.resident import Daemon, DaemonOptions

    root = str(Path.cwd().resolve())
    mode_name = (getattr(args, "resident_mode", "") or "default").strip()
    mode, ok = parse_mode(mode_name)
    if not ok:
        print(
            f"未知权限模式: {mode_name}（可选 default / acceptEdits / plan / bypassPermissions）",
            file=sys.stderr,
        )
        return 2

    opts = DaemonOptions(
        root=root,
        interval=max(1.0, float(getattr(args, "interval", 300.0) or 300.0)),
        max_tasks_per_round=max(1, int(getattr(args, "resident_batch", 5) or 5)),
        max_turns=max(1, int(getattr(args, "resident_turns", 20) or 20)),
        timeout=max(1.0, float(getattr(args, "resident_timeout", 900.0) or 900.0)),
        mode=mode,
        provider_index=max(0, int(getattr(args, "provider_index", 0) or 0)),
        extra_allowed=list(getattr(args, "resident_allow", None) or []),
        notify=not bool(getattr(args, "no_notify", False)),
        mail=bool(getattr(args, "mail_watch", False)),
        mail_allow_write=bool(getattr(args, "mail_allow_write", False)),
    )
    daemon = Daemon(cfg, opts)
    if not daemon.acquire_lock():
        return 1
    once = bool(getattr(args, "resident_once", False))
    try:
        if once and opts.mail:
            # 轻量预检：先只连 IMAP，不启动 TaskRunner。
            # TaskRunner 要拉起 MCP 子进程等，空跑约 4 秒；补信轮询每 3 分钟一次，
            # 大部分轮次没有新邮件，没必要为此付启动开销。
            daemon.ingest_mail()
            if not daemon.has_pending():
                return 0
        await daemon.start()
        if once:
            handled = await daemon.run_once()
            daemon.log(f"一次性运行结束，处理 {handled} 条任务")
        else:
            await daemon.run_forever()
    except KeyboardInterrupt:
        daemon.log("收到 Ctrl+C，退出")
    finally:
        await daemon.close()
        daemon.release_lock()
    return 0


async def _amain(member_args=None) -> int:
    if member_args is not None and getattr(member_args, "worktree", ""):
        os.chdir(member_args.worktree)

    # 常驻模式的三个入口都在 TUI 之前处理：它们不需要界面，也不加载 textual。
    if member_args is not None:
        early = await _resident_early_exit(member_args)
        if early is not None:
            return early
        if getattr(member_args, "check_mail", False):
            # 拉信不需要 LLM 配置，所以放在读配置文件之前：邮箱填对了但 api_key
            # 还没填时，也应该能先确认收信链路是通的。
            return await _check_mail(str(Path.cwd().resolve()))

    explicit = getattr(member_args, "config", "") or None
    init_only = bool(getattr(member_args, "init", False))
    try:
        cfg_path, status = _prepare_config(explicit, init_only)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if status == STATUS_INIT:
        return 0
    if status == STATUS_NEEDS_KEY:
        print(f'填入 api_key 后重新运行: lcode --config "{cfg_path}"', file=sys.stderr)
        return 1

    try:
        cfg = load(str(cfg_path))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # 常驻模式：不加载 TUI，直接跑无头循环。
    if member_args is not None and (
        getattr(member_args, "resident", False) or getattr(member_args, "resident_once", False)
    ):
        return await _run_resident(cfg, member_args)

    # Imported lazily so a config error is reported before pulling in the TUI stack.
    from lcode import mcp as mcp_client
    from lcode.hook import load as load_hooks
    from lcode.tui.app import LcodeApp

    registry = new_default_registry(cfg.vision, cfg.audio)
    root = str(Path.cwd().resolve())

    # 内置 stdio MCP server（如 lcode.mcp_servers.web_search）需要「装着 lcode 的解释器」，
    # 允许 .lcode.yaml 里写 command: "${LCODE_PYTHON}"，不必把绝对路径写死。
    os.environ.setdefault("LCODE_PYTHON", sys.executable)

    mcp_cfg = mcp_client.load_config(root)
    mcp_mgr = await mcp_client.new_manager(mcp_cfg, __version__)

    # 项目指令 + 长期记忆索引（启动时加载一次）。
    instruction_text = instructions_pkg.Loader(root).load()
    mem_mgr = memory_pkg.Manager(
        project_dir=str(Path(root) / ".lcode" / "memory"),
        user_dir=str(Path.home() / ".lcode" / "memory"),
    )
    memory_text = mem_mgr.load_index()

    # Skill catalog：启动期扫描项目级与用户级目录。
    from lcode import coordinator
    from lcode.agent.agent_tool import AgentTool
    from lcode.subagent import load_catalog
    from lcode.task import Manager, SendMessageTool, TaskGetTool, TaskListTool, TaskStopTool
    from lcode.team import AgentNameRegistry
    from lcode.team import Manager as TeamManager
    from lcode.team.tools import team_tools
    from lcode.tool.install_skill import InstallSkillTool
    from lcode.tool.load_skill import LoadSkillTool

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
    from lcode import worktree as worktree_pkg

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
    sessions_dir = str(Path(root) / ".lcode" / "sessions")
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
            from lcode.team.member import run_team_member

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

        app = LcodeApp(
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
                print(f"Lcode 启动失败: {exc}", file=sys.stderr)
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
        for entry in (".lcode/worktrees/", ".lcode/worktree_session.json")
        if entry not in lines
    ]
    if missing:
        print(
            "Worktree 提示: .gitignore 缺少 " + "、".join(missing),
            file=sys.stderr,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lcode", description="Lcode 终端 Agent")
    parser.add_argument("--version", action="version", version=f"Lcode {__version__}")
    parser.add_argument(
        "--init", action="store_true", help="生成用户级配置模板 ~/.lcode/config.yaml 后退出"
    )
    parser.add_argument("--config", default="", help="指定配置文件路径（覆盖自动查找）")
    parser.add_argument("--team-member", action="store_true", help="以 Team 队员模式运行")
    parser.add_argument("--team", default="", help="Team 名")
    parser.add_argument("--member", default="", help="队员名")
    parser.add_argument("--agent-id", default="", help="队员 agent_id")
    parser.add_argument("--session-dir", default="", help="队员 session 目录")
    parser.add_argument("--worktree", default="", help="队员 worktree 目录")
    parser.add_argument("--agent-type", default="", help="Agent 角色名")
    parser.add_argument("--model", default="", help="模型覆盖")
    parser.add_argument("--plan-mode", action="store_true", help="以 plan 模式启动")

    resident = parser.add_argument_group("常驻模式")
    resident.add_argument("--enqueue", default="", help="把一条任务加入常驻队列后退出")
    resident.add_argument(
        "--tasks", dest="resident_tasks", action="store_true", help="列出常驻队列后退出"
    )
    resident.add_argument("--resident", action="store_true", help="常驻循环：轮询队列并执行任务")
    resident.add_argument(
        "--resident-once",
        action="store_true",
        help="只处理一轮就退出（供任务计划程序 / CI 调度）",
    )
    resident.add_argument(
        "--interval", type=float, default=300.0, help="常驻轮询间隔秒数（默认 300）"
    )
    resident.add_argument(
        "--resident-mode",
        default="default",
        help="常驻任务的权限模式：default(只读) / acceptEdits / plan / bypassPermissions",
    )
    resident.add_argument(
        "--resident-turns", type=int, default=20, help="单条任务最大迭代轮数（默认 20）"
    )
    resident.add_argument(
        "--resident-batch",
        type=int,
        default=5,
        help="单轮最多处理几条任务（默认 5）",
    )
    resident.add_argument(
        "--resident-timeout", type=float, default=900.0, help="单条任务超时秒数（默认 900）"
    )
    resident.add_argument(
        "--resident-allow",
        action="append",
        default=[],
        metavar="TOOL",
        help="额外允许的工具名（可重复）",
    )
    resident.add_argument(
        "--no-notify",
        dest="no_notify",
        action="store_true",
        help="不发送任务完成/失败通知（桌面横幅），只写日志",
    )

    mail = parser.add_argument_group("收件箱")
    mail.add_argument(
        "--mail-watch",
        dest="mail_watch",
        action="store_true",
        help="常驻时每轮先拉收件箱，把 [前缀] 邮件变成任务并回信",
    )
    mail.add_argument(
        "--check-mail",
        dest="check_mail",
        action="store_true",
        help="只拉一次收件箱并入队，然后退出（供任务计划程序调度）",
    )
    mail.add_argument(
        "--mail-allow-write",
        dest="mail_allow_write",
        action="store_true",
        help="允许邮件来源的任务写文件/执行命令（默认只读）",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(_amain(args)))
    except KeyboardInterrupt:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
