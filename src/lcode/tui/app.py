"""Textual application for lcode."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import Button, OptionList, Static

from lcode import coordinator
from lcode.agent import Agent, ApprovalRequest, Phase
from lcode.agent.runtime import SessionRuntime
from lcode.command import register_skill_commands
from lcode.config import ProviderConfig, effective_context_window
from lcode.hook import Engine as HookEngine
from lcode.hook import Event as HookEvent
from lcode.llm import Provider, new_provider
from lcode.permission import Mode, Outcome
from lcode.permission.engine import Engine
from lcode.prompt import EXECUTE_DIRECTIVE, render_banner
from lcode.skills import RuntimeSkillAgent, SkillExecutor, SkillLoader
from lcode.subagent import Catalog
from lcode.task import Manager as TaskManager
from lcode.tool import Registry
from lcode.tool.ctx import with_cwd
from lcode.tool.filter import TEAM_ONLY_TOOLS
from lcode.tui import attach as attach_mod
from lcode.tui.attach import (
    MAX_ATTACHMENTS,
    AttachError,
    Attachment,
    attach_summary,
    classify,
    dedupe,
    render_chips,
)
from lcode.tui.attachments import prepare_attachments
from lcode.tui.clipboard import set_clipboard
from lcode.tui.commands import CommandMixin
from lcode.tui.complete import CompletionMenu, CompletionMixin
from lcode.tui.logview import LogView
from lcode.tui.resume import (
    begin_resume,
    handle_resume_key,
    select_session,
)
from lcode.tui.select import option_label
from lcode.tui.stream import ChatInput
from lcode.tui.tasks import consume_lead_mail, consume_task_done
from lcode.tui.team_adapter import TeamAdapter
from lcode.tui.view import (
    approval_block,
    error_block,
    render_markdown,
    render_statusbar,
    tool_line,
    tool_result_summary,
    user_block,
)
from lcode.tui.worktree_adapter import WorktreeAdapter
from lcode.worktree import Manager as WorktreeManager


class SessionState(Enum):
    SELECTING = "selecting"
    IDLE = "idle"
    STREAMING = "streaming"
    APPROVING = "approving"
    RESUMING = "resuming"


@dataclass
class ToolDisplay:
    name: str
    args: str


class LcodeApp(CompletionMixin, CommandMixin, App):
    CSS = """
    #log {
        height: 1fr;
    }

    #streaming {
        height: auto;
    }

    #select {
        height: 1fr;
        display: none;
    }

    #resume-query {
        height: 1;
        padding: 0 1;
        display: none;
    }

    #completion {
        height: auto;
        padding: 0 1;
    }

    #input-row {
        height: auto;
        border: round $accent;
        padding: 0 1;
    }

    #prompt {
        width: auto;
        color: $accent;
        padding: 1 0;
    }

    #input {
        border: none;
        width: 1fr;
        height: auto;
        min-height: 1;
        max-height: 6;
        padding: 0;
    }

    #statusbar {
        height: 1;
        padding: 0 1;
    }

    #attach-row {
        height: auto;
        padding: 0 1;
    }

    #attach-bar {
        height: auto;
    }

    #attach-btn {
        width: auto;
        min-width: 7;
        height: 1;
        border: none;
        background: $accent 20%;
        color: $text;
    }
    """

    def __init__(
        self,
        providers: list[ProviderConfig],
        version: str,
        registry: Registry,
        engine: Engine,
        writer=None,
        mem_mgr=None,
        instruction_text: str = "",
        memory_text: str = "",
        sessions_dir: str = "",
        workspace: str = "",
        ses_ctx=None,
        context_window: int = 0,
        skill_loader: SkillLoader | None = None,
        load_skill_tool=None,
        install_skill_tool=None,
        hook_engine: HookEngine | None = None,
        task_mgr: TaskManager | None = None,
        subagent_catalog: Catalog | None = None,
        worktree_mgr: WorktreeManager | None = None,
        team_mgr=None,
        coordinator_mode: bool = False,
    ) -> None:
        super().__init__()
        self.providers = providers
        self._version = version
        self._tool_registry = registry
        self._engine = engine
        self.provider: Provider | None = None
        self.writer = writer
        self.mem_mgr = mem_mgr
        self.instruction_text = instruction_text
        self.memory_text = memory_text
        self.workspace = workspace or os.getcwd()
        self.sessions_dir = sessions_dir or str(Path(self.workspace) / ".lcode" / "sessions")
        self.ses_ctx = ses_ctx
        self.context_window = context_window
        # 未传入 loader 时保持空 catalog（测试/嵌入式场景），不主动扫描磁盘。
        self.skill_loader = skill_loader or SkillLoader(self.workspace)
        self.hook_engine = hook_engine
        self.task_mgr = task_mgr
        self.subagent_catalog = subagent_catalog
        self.worktree_mgr = worktree_mgr
        self.team_mgr = team_mgr
        self.coordinator_mode = coordinator_mode
        self.active_cwd = ""
        if worktree_mgr is not None:
            session = worktree_mgr.current_session()
            if session is not None:
                self.active_cwd = session.worktree_path
        self._worktree_adapter = (
            WorktreeAdapter(worktree_mgr, self._set_active_cwd)
            if worktree_mgr is not None
            else None
        )
        self._team_adapter = TeamAdapter(team_mgr) if team_mgr is not None else None
        self._lead_mail_task: asyncio.Task | None = None
        if task_mgr is not None:
            # 子 Agent 的 ASK 升级回主 TUI；前台 inline 路径同样生效。
            task_mgr.set_approval_handler(self._upgrade_subagent_approval)
        # 跨轮次共享：turn_count 让记忆提取的"每 5 轮"在多次 Agent 重建后仍然成立。
        self.runtime = SessionRuntime(
            memory_manager=mem_mgr,
            instruction_text=instruction_text,
            memory_text=memory_text,
            session=ses_ctx,
            context_window=context_window,
            skill_catalog=self.skill_loader,
            hook_engine=hook_engine,
        )
        # Agent 每轮重建，Skill 激活态与 LoadSkill 都通过 runtime 门面共享。
        self.skill_agent = RuntimeSkillAgent(self.runtime)
        self.skill_executor = SkillExecutor(
            self.skill_loader,
            self.runtime,
            registry=registry,
            engine=engine,
            version=version,
        )
        self._load_skill_tool = load_skill_tool
        self._install_skill_tool = install_skill_tool
        if load_skill_tool is not None:
            load_skill_tool.set_loader(self.skill_loader)
            load_skill_tool.set_agent(self.skill_agent)
        # 恢复会话时若 token 超限，交给这个可注入的压缩器（ch08 编排层就绪后接入）。
        self.compact_fn = None

        self.conv = self._bind_conversation(writer)
        self._init_command_system()
        if install_skill_tool is not None:
            install_skill_tool.configure(self.skill_loader, self._refresh_skill_commands)
        self.completion = CompletionMenu()
        self.state = SessionState.IDLE
        self._mode: Mode = engine.start_mode
        self.cur_reply = ""
        self.cur_tools: list[ToolDisplay] = []
        self.iter = 0
        self._usage_in = 0
        self._usage_out = 0
        self.turn_start = 0.0
        self._stream_task: asyncio.Task | None = None
        self._timer: Timer | None = None
        self.turn_cancel: asyncio.Event | None = None
        self.pending = None
        self.approve_cursor = 0
        # 子 Agent 审批的响应目的地：IDLE 时不能把界面留在 STREAMING。
        self._subagent_approval = False
        # /resume 选择列表状态
        self.resume_items: list = []
        self.resume_visible: list = []
        self.resume_query = ""
        self._resume_task: asyncio.Task | None = None
        self._task_done_task: asyncio.Task | None = None
        self._main_agent: Agent | None = None
        # 待附加的文件（按钮选择或拖入）：下一轮发送时先转成文字再发给主模型。
        self.attachments: list[Attachment] = []
        self._attach_task: asyncio.Task | None = None
        # SessionStart/SessionEnd 每个进程最多派发一次（cli 退出兜底也靠这个去重）。
        self._session_started = False
        self._session_ended = False

    # ── composition ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield LogView(id="log", wrap=True, markup=True, highlight=True)
        yield Static("", id="streaming")
        yield Static("", id="resume-query")
        yield OptionList(id="select")
        yield Static("", id="attach-bar")
        with Horizontal(id="input-row"):
            yield Static("❯", id="prompt")
            yield ChatInput(id="input", placeholder="Send a message...")
            yield Button("📎 附加", id="attach-btn", variant="default")
        yield Static("", id="completion")
        yield Static("", id="statusbar")

    async def on_mount(self) -> None:
        log = self.query_one("#log", LogView)
        log.write(Text(render_banner(self._version, self.workspace)))

        if self.task_mgr is not None:
            self._task_done_task = asyncio.create_task(self._consume_task_done())
        if self.team_mgr is not None:
            self._lead_mail_task = asyncio.create_task(consume_lead_mail(self))
        if len(self.providers) == 1:
            self._apply_provider(self.providers[0])
            self.state = SessionState.IDLE
            self._update_statusbar()
            self._show_conversation()
            self.query_one("#input", ChatInput).focus()
            await self._start_session("startup")
        else:
            self._enter_selecting()

    def on_unmount(self) -> None:
        # 退出时关闭当前会话的 JSONL 句柄（/resume 后 writer 可能已经换过）。
        if self.hook_engine is not None:
            self.hook_engine.cancel_pending()
        if self.writer is not None:
            try:
                self.writer.close()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass

    async def _start_session(self, source: str) -> None:
        if self._session_started:
            return
        self._session_started = True
        await self._dispatch_hook_event(HookEvent.SESSION_START, source=source)

    async def ensure_session_end(self, reason: str = "exit") -> None:
        # cli 退出兜底 + action_quit 共用；SessionEnd 失败不得阻塞退出。
        if self._session_ended:
            return
        self._session_ended = True
        try:
            await self._dispatch_hook_event(HookEvent.SESSION_END, reason=reason)
        except Exception as exc:  # noqa: BLE001 - shutdown must not raise
            print(f"hook SessionEnd failed: {exc}", file=sys.stderr)

    @property
    def main_agent(self) -> Agent | None:
        # 最近一次主对话 Agent（每轮重建）；供外部回填 AgentTool parent。
        return self._main_agent

    def _set_active_cwd(self, path: str) -> None:
        self.active_cwd = path

    def _effective_cwd(self) -> str:
        return self.active_cwd or str(Path.cwd())

    def worktree_accessor(self):
        return self._worktree_adapter

    def team_accessor(self):
        return self._team_adapter

    async def begin_autonomous_turn(self) -> None:
        if not self.idle() or self.provider is None:
            return
        self.notify_system("● [team-update] 队员发来新消息，自动开始处理…")
        await self._start_turn("[team-update] 队员发来新消息，请按 Coordinator 流程处理。")

    def _apply_provider(self, cfg: ProviderConfig) -> None:
        # 连接 provider，同时把模型名/上下文窗口/记忆更新用的 provider 一起接上。
        self.provider = new_provider(cfg)
        self.context_window = effective_context_window(cfg)
        self.runtime.context_window = self.context_window
        if self.writer is not None:
            self.writer.model = self.provider.model
        if self.mem_mgr is not None:
            self.mem_mgr.set_provider(self.provider, self.provider.model)
        self.skill_executor.set_provider(self.provider)

    def _refresh_skill_commands(self) -> None:
        # 安装/重载 Skill 后立即同步 /help、补全菜单与 dispatcher。
        register_skill_commands(self.cmd_registry, self.skill_loader, self.skill_executor)

    # ── key handling ─────────────────────────────────────────────────────

    async def on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "ctrl+c":
            if self.state in (SessionState.STREAMING, SessionState.APPROVING):
                # 正在回答/等待审批时，Ctrl+C 永远是"中断"，避免手滑丢内容。
                self._cancel_turn()
                return
            if self._copy_selected_text():
                return
            await self.action_quit()
            return
        if self.state is SessionState.RESUMING:
            handle_resume_key(self, event)
            return
        if key == "escape":
            if self.state in (SessionState.STREAMING, SessionState.APPROVING):
                self._cancel_turn()
            elif self.attachments:
                # 空闲时 Esc 清空待发送附件（提示里也是这么写的）。
                self.clear_attachments()
            return
        if key == "shift+tab":
            if self.state is SessionState.IDLE:
                self._mode = Mode((int(self._mode) + 1) % 4)
                self._update_statusbar()
                self.query_one("#log", LogView).write(
                    Text(f"已切换到 {self._mode} 权限模式", style="dim")
                )
            return
        if self.state is SessionState.APPROVING:
            self._handle_approval_key(key)

    def _handle_approval_key(self, key: str) -> None:
        if key in ("up", "k"):
            self.approve_cursor = (self.approve_cursor - 1) % 3
            self._refresh_streaming_view()
        elif key in ("down", "j"):
            self.approve_cursor = (self.approve_cursor + 1) % 3
            self._refresh_streaming_view()
        elif key in ("enter", "space"):
            self._submit_approval(self.approve_cursor)
        elif key == "1":
            self._submit_approval(0)
        elif key == "2":
            self._submit_approval(1)
        elif key == "3":
            self._submit_approval(2)
        elif key == "y":
            self._submit_approval(0)
        elif key in ("n", "d"):
            self._submit_approval(2)

    async def _consume_task_done(self) -> None:
        # 常驻消费后台任务完成队列（T26）；逻辑在 tui.tasks 中便于单测。
        await consume_task_done(self)

    async def _upgrade_subagent_approval(self, req):
        # 子 Agent 的审批升级：复用现有 Approval 弹窗，标注来源后等待用户选择。
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        reason = req.reason
        if not reason.startswith("[来自 SubAgent"):
            reason = f"[来自 SubAgent] {reason}"
        self.pending = ApprovalRequest(
            name=req.name,
            args=req.args,
            reason=reason,
            respond=future,
        )
        self.approve_cursor = 0
        self._subagent_approval = True
        self.state = SessionState.APPROVING
        self._refresh_streaming_view()
        return await future

    def _submit_approval(self, index: int) -> None:
        outcomes = [Outcome.ALLOW_ONCE, Outcome.ALLOW_FOREVER, Outcome.DENY_ONCE]
        pending = self.pending
        self.pending = None
        if self._subagent_approval:
            # 后台子 Agent 的审批可能在主对话 IDLE 时弹出。
            self._subagent_approval = False
            self.state = (
                SessionState.STREAMING if self._stream_task is not None else SessionState.IDLE
            )
            if self.state is SessionState.IDLE:
                self.query_one("#streaming", Static).update("")
        else:
            self.state = SessionState.STREAMING
        if pending is not None and pending.respond is not None and not pending.respond.done():
            pending.respond.set_result(outcomes[index])

    # ── visibility ───────────────────────────────────────────────────────

    # ── attachments ──────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # 只处理附件按钮；其它按钮（将来若有）留给各自的 handler。
        if event.button.id != "attach-btn":
            return
        event.stop()
        self._start_attach_pick()

    def _start_attach_pick(self) -> None:
        if self.state is SessionState.STREAMING:
            self.notify_system("（正在回答，稍后再附加文件）")
            return
        if len(self.attachments) >= MAX_ATTACHMENTS:
            self.notify_system(f"（最多附加 {MAX_ATTACHMENTS} 个文件，先发送或按 Esc 清空）")
            return
        # 选择器是阻塞子进程：放后台任务，界面不冻。
        self._attach_task = asyncio.create_task(self._run_attach_pick())

    async def _run_attach_pick(self) -> None:
        try:
            # 走模块属性而不是导入的名字：测试能 monkeypatch 掉，
            # 也避免在无 GUI 环境里真的弹系统对话框。
            paths = await attach_mod.pick_files()
        except AttachError as exc:
            self.notify_system(f"（{exc}）")
            return
        except Exception as exc:  # noqa: BLE001 - 选择器失败不能打断会话
            self.notify_system(f"（打开文件选择器失败: {exc}）")
            return
        if not paths:
            return  # 用户取消
        self.add_attachments(paths)

    def add_attachments(self, paths: list[str]) -> None:
        """Queue files for the next turn (also used by drag-and-drop / tests)."""
        added: list[Attachment] = []
        problems: list[str] = []
        for path in paths:
            try:
                added.append(classify(path))
            except AttachError as exc:
                problems.append(str(exc))
        merged = dedupe(self.attachments + added)[:MAX_ATTACHMENTS]
        self.attachments = merged
        self._refresh_attach_bar()
        if problems:
            self.notify_system("（" + "；".join(problems) + "）")

    def clear_attachments(self) -> None:
        if not self.attachments:
            return
        self.attachments = []
        self._refresh_attach_bar()
        self.notify_system("（已清空附件）")

    def _refresh_attach_bar(self) -> None:
        try:
            bar = self.query_one("#attach-bar", Static)
        except Exception:  # noqa: BLE001 - not mounted yet
            return
        bar.update(render_chips(self.attachments))

    def _show_selecting(self) -> None:
        for wid in ("#log", "#streaming", "#input-row", "#statusbar", "#attach-bar"):
            self.query_one(wid).display = False
        self.query_one("#select").display = True

    def _show_conversation(self) -> None:
        for wid in ("#log", "#streaming", "#input-row", "#statusbar", "#attach-bar"):
            self.query_one(wid).display = True
        self.query_one("#select").display = False

    # ── provider selection ───────────────────────────────────────────────

    def _enter_selecting(self) -> None:
        self.state = SessionState.SELECTING
        option_list = self.query_one("#select", OptionList)
        option_list.clear_options()
        for cfg in self.providers:
            option_list.add_option(option_label(cfg))
        option_list.highlighted = 0
        option_list.focus()
        self._show_selecting()

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if self.state is SessionState.RESUMING:
            select_session(self, event.option_index)
            return
        if self.state is not SessionState.SELECTING:
            return
        idx = event.option_index
        self._apply_provider(self.providers[idx])
        self.state = SessionState.IDLE
        self._update_statusbar()
        self._show_conversation()
        self.query_one("#input", ChatInput).focus()
        await self._start_session("startup")

    # ── status bar ───────────────────────────────────────────────────────

    def _update_statusbar(self) -> None:
        statusbar = self.query_one("#statusbar", Static)
        if self.provider is None:
            statusbar.update("")
            return
        statusbar.update(
            render_statusbar(
                self._mode,
                self.provider.model,
                usage_in=self._usage_in,
                usage_out=self._usage_out,
                coordinator=self.coordinator_mode,
            )
        )

    # ── clipboard ────────────────────────────────────────────────────────

    def copy_text(self, text: str) -> None:
        # 双保险：OSC 52 覆盖 SSH/远程终端，平台原生写入覆盖屏蔽 OSC 52 的终端。
        if not text:
            return
        self.copy_to_clipboard(text)
        set_clipboard(text)

    def _copy_selected_text(self) -> bool:
        # 返回 True 表示这次 Ctrl+C 被当作"复制"消费掉了。
        try:
            text = self.screen.get_selected_text()
        except Exception:  # noqa: BLE001 - 选区状态异常不该阻断按键
            return False
        if not text:
            return False
        self.copy_text(text)
        try:
            self.notify(f"已复制 {len(text)} 字符", timeout=2)
        except Exception:  # noqa: BLE001 - 通知失败不影响复制
            pass
        # 复制后清掉选区：下一次 Ctrl+C 仍能中断/退出，不会一直"只复制"。
        self.clear_selection()
        return True

    def last_assistant_message(self) -> str:
        # 流式输出的内容还没落进 conv，优先返回它，这样回答到一半也能复制。
        if self.cur_reply.strip():
            return self.cur_reply
        for msg in reversed(self.conv.messages()):
            if msg.role == "assistant" and msg.content:
                return msg.content
        return ""

    # ── input submission ─────────────────────────────────────────────────

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        await self.submit(event.text)

    async def submit(self, text: str) -> None:
        text = text.strip()
        # 只有附件没有文字也是合法输入（「看看这个」不必写出来）。
        if not text and not self.attachments:
            return
        # 斜杠命令优先，且不吃附件：/help 之类不该因为挂着附件就走模型。
        if text and await self.dispatch_slash(text):
            try:
                self.query_one("#input", ChatInput).load_text("")
            except Exception:  # noqa: BLE001
                pass
            self.completion.hide()
            self._render_completion()
            return
        if self.state is SessionState.STREAMING:
            return

        prompt = text
        if self.attachments:
            prompt = await self._expand_attachments(text)
            if prompt is None:
                return  # 附件全部读取失败，已在日志里说明原因

        # UserPromptSubmit 是可拦截事件：blocked 时保留输入内容，不写入对话历史。
        pre = await self._dispatch_hook_event(HookEvent.USER_PROMPT_SUBMIT, prompt=prompt)
        if pre.blocked:
            name = pre.blocking_hook_name
            reason = pre.reason or "blocked"
            self.query_one("#log", LogView).write(error_block(f"[hook {name}] {reason}"))
            return
        await self._start_turn(prompt)

    async def _expand_attachments(self, text: str) -> str | None:
        """把待发送的附件转成文字，拼进提示词。返回 None 表示全部失败。"""
        items = list(self.attachments)
        label = attach_summary(items)
        # 先清空队列：转写可能失败/中断，不该让同样的文件留在那里反复重试。
        self.attachments = []
        self._refresh_attach_bar()
        self.notify_system(f"● 正在处理附件：{label}")

        prepared = await prepare_attachments(items, self._tool_registry, question=text)
        if not prepared.blocks:
            reason = "；".join(prepared.errors) or "没有可读内容"
            self.query_one("#log", LogView).write(
                error_block(f"附件无法读取：{reason}")
            )
            return None
        for line in prepared.errors:
            self.notify_system(f"（部分附件未读取：{line}）")
        return prepared.prompt(text)

    def enter_plan_mode(self) -> None:
        self._mode = Mode.PLAN
        self.notify_system("（已进入计划模式，仅只读工具）")
        self._update_statusbar()

    async def start_execute(self) -> None:
        self._mode = Mode.DEFAULT
        await self._start_turn(EXECUTE_DIRECTIVE)

    def notify_system(self, text: str) -> None:
        self.query_one("#log", LogView).write(Text(text, style="dim"))

    async def _start_turn(self, user_text: str) -> None:
        self.conv.add_user(user_text)
        self.query_one("#log", LogView).write(user_block(user_text))
        self.query_one("#input", ChatInput).load_text("")

        self.cur_reply = ""
        self.cur_tools = []
        self.iter = 0
        self.turn_start = time.monotonic()
        self.state = SessionState.STREAMING
        self.turn_cancel = asyncio.Event()
        self._stream_task = asyncio.create_task(self._consume_events())
        self._timer = self.set_interval(0.1, self._tick)

    # ── streaming ────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self.state is SessionState.STREAMING:
            self._refresh_streaming_view()

    def _refresh_streaming_view(self) -> None:
        streaming = self.query_one("#streaming", Static)
        if self.state is SessionState.APPROVING and self.pending is not None:
            streaming.update(approval_block(self.pending, self.approve_cursor))
            return
        elapsed = int(time.monotonic() - self.turn_start)
        content = Text()
        if self.cur_tools:
            for tool in self.cur_tools:
                content.append(f"● {tool.name}({tool.args})", style="bold cyan")
                content.append(" Running…\n", style="dim")
            content.append(f"({elapsed}s)", style="dim")
        else:
            if self.cur_reply:
                content.append(self.cur_reply)
                content.append("\n")
            if self.iter > 0:
                content.append(f"Imagining… ({elapsed}s · 第 {self.iter} 轮)", style="dim")
            else:
                content.append(f"Imagining… ({elapsed}s)", style="dim")
        streaming.update(content)

    async def _consume_events(self) -> None:
        assert self.provider is not None
        # 每轮重新读取 MEMORY.md 索引，让后台新写入的笔记在下一轮就能注入。
        self.runtime.memory_text = (
            self.mem_mgr.load_index() if self.mem_mgr is not None else self.memory_text
        )
        agent = Agent(
            self.provider,
            self._tool_registry,
            self._version,
            self._engine,
            runtime=self.runtime,
            hook_engine=self.hook_engine,
        )
        self._main_agent = agent
        if self.coordinator_mode:
            agent.set_allowed_tools(coordinator.allowed_tools())
            agent.append_system_prompt(coordinator.system_prompt_suffix())
        else:
            agent.set_allowed_tools(
                [
                    definition.name
                    for definition in self._tool_registry.definitions()
                    if definition.name not in TEAM_ONLY_TOOLS
                ]
            )
        agent_tool = self._tool_registry.get("Agent")
        if agent_tool is not None and hasattr(agent_tool, "set_parent"):
            agent_tool.set_parent(agent, self.conv)
        try:
            # explicit cwd：主 Agent 的每次工具调用都以 active_cwd 解析相对路径。
            with with_cwd(self._effective_cwd()):
                async for ev in agent.run(self.conv, self._mode, self.turn_cancel):
                    if ev.err is not None:
                        self._finish_with_error(ev.err)
                        return
                    if ev.approval is not None:
                        self.pending = ev.approval
                        self.approve_cursor = 0
                        self.state = SessionState.APPROVING
                        self._refresh_streaming_view()
                        continue
                    if ev.tool is not None:
                        self._handle_tool_event(ev.tool)
                    if ev.usage is not None:
                        self._usage_in += ev.usage.input
                        self._usage_out += ev.usage.output
                        self._update_statusbar()
                    if ev.notice:
                        self.query_one("#log", LogView).write(Text(ev.notice, style="dim"))
                    if ev.iter > 0:
                        self.iter = ev.iter
                    if ev.text:
                        self.cur_reply += ev.text
                        self._refresh_streaming_view()
                    if ev.done:
                        self._commit_final()
                        return
            self._commit_final()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._finish_with_error(exc)

    def _handle_tool_event(self, tool_event) -> None:
        if tool_event.phase is Phase.START:
            if self.cur_reply:
                self._commit_preamble()
            self.cur_tools.append(ToolDisplay(name=tool_event.name, args=tool_event.args))
            self._refresh_streaming_view()
        else:
            log = self.query_one("#log", LogView)
            if self.cur_tools:
                display = self.cur_tools.pop(0)
            else:
                display = ToolDisplay(name=tool_event.name, args=tool_event.args)
            log.write(tool_line(display.name, display.args))
            log.write(tool_result_summary(tool_event.result, tool_event.is_error))
            self._refresh_streaming_view()

    def _commit_preamble(self) -> None:
        if self.cur_reply:
            self.query_one("#log", LogView).write(render_markdown(self.cur_reply))
            self.cur_reply = ""

    def _commit_final(self) -> None:
        elapsed = time.monotonic() - self.turn_start
        log = self.query_one("#log", LogView)
        if self.cur_reply:
            log.write(render_markdown(self.cur_reply))
        log.write(Text(f"· {elapsed:.1f}s", style="dim"))
        self._reset_after_turn()

    def _finish_with_error(self, err: Exception) -> None:
        self.query_one("#log", LogView).write(error_block(err))
        self._reset_after_turn()

    def _reset_after_turn(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._stream_task = None
        self.cur_reply = ""
        self.cur_tools = []
        self.iter = 0
        self.turn_cancel = None
        self.pending = None
        self._subagent_approval = False
        self.query_one("#streaming", Static).update("")
        self.state = SessionState.IDLE

    # ── resume ───────────────────────────────────────────────────────────

    def begin_resume(self) -> None:
        begin_resume(self)

    async def compact_history(self, msgs):
        # ch08 的 manage_context 就绪后由 cli 注入；当前默认不可用。
        if self.compact_fn is None:
            return None
        return await self.compact_fn(msgs)

    def _redraw_conversation(self) -> None:
        # 恢复会话后重绘最近的历史（只显示最近 30 条，避免刷屏）。
        log = self.query_one("#log", LogView)
        log.clear()
        log.write(Text(render_banner(self._version, self.workspace)))
        messages = self.conv.messages()
        shown = messages[-30:]
        if len(messages) > len(shown):
            log.write(Text(f"（仅显示最近 {len(shown)} 条消息）", style="dim"))
        for msg in shown:
            if msg.role == "user":
                log.write(user_block(msg.content))
            elif msg.role == "assistant" and msg.content:
                log.write(render_markdown(msg.content))

    # ── cancel / quit ────────────────────────────────────────────────────

    def _cancel_turn(self) -> None:
        if (
            self.pending is not None
            and self.pending.respond is not None
            and not self.pending.respond.done()
        ):
            self.pending.respond.set_result(Outcome.DENY_ONCE)
            self.pending = None
        if self._subagent_approval and self._stream_task is None:
            # 后台子 Agent 在主对话 IDLE 时弹出的审批：取消后直接回到 IDLE。
            self._subagent_approval = False
            self.state = SessionState.IDLE
            self.query_one("#streaming", Static).update("")
        if self.turn_cancel is not None:
            self.turn_cancel.set()

    async def action_quit(self) -> None:
        # 退出前先给 SessionEnd hook 一次执行机会，再取消后台任务。
        await self.ensure_session_end("exit")
        self.skill_executor.cancel_pending()
        if self.hook_engine is not None:
            self.hook_engine.cancel_pending()
        for task in (
            self._stream_task,
            self._resume_task,
            self._compact_task,
            self._selected_task,
            self._image_task,
            self._audio_task,
            self._attach_task,
            self._task_done_task,
            self._lead_mail_task,
        ):
            if task is None or task.done():
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass
        self._stream_task = None
        self._task_done_task = None
        self._lead_mail_task = None
        if self.hook_engine is not None:
            await self.hook_engine.aclose()
        self.exit()
