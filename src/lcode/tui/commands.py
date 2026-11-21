"""Slash-command glue: LcodeApp's command.UI implementation and dispatcher."""

from __future__ import annotations

import asyncio
import json
import time

from rich.text import Text

from lcode.command import (
    HookSummary,
    Kind,
    Registry,
    SkillSummary,
    parse_with_args,
    register_builtins,
    register_skill_commands,
    register_skill_management,
)
from lcode.compact import new_session_context
from lcode.conversation import Conversation
from lcode.hook import DispatchResult
from lcode.hook import Event as HookEvent
from lcode.llm import Message
from lcode.permission import Mode
from lcode.prompt import render_banner
from lcode.session import open_writer
from lcode.tui.logview import LogView
from lcode.tui.view import error_block, render_markdown, user_block

UNKNOWN_COMMAND_HINT = "未知命令{name}。输入 /help 查看可用命令"


class CommandMixin:
    # 把 LcodeApp 的能力收敛成 command.UI；handler 不接触 Textual 类型。

    # ── setup ────────────────────────────────────────────────────────────

    def _init_command_system(self) -> None:
        # 启动期注册并做冲突检测；冲突会直接抛 RuntimeError 终止启动。
        self.cmd_registry = Registry()
        register_builtins(self.cmd_registry)
        # /skill 管理命令 + 每个 skill 的短命令；skill 同名时覆盖旧 handler。
        register_skill_management(self.cmd_registry, self.skill_loader, self.skill_executor)
        register_skill_commands(self.cmd_registry, self.skill_loader, self.skill_executor)
        self._pending_println: list[tuple[str, str]] = []
        self._compact_task: asyncio.Task | None = None
        self._selected_task: asyncio.Task | None = None
        self._image_task: asyncio.Task | None = None
        self._audio_task: asyncio.Task | None = None
        self._command_args = ""

    # ── UI: output ───────────────────────────────────────────────────────

    def println(self, msg: str) -> None:
        self._pending_println.append(("info", msg))

    def error(self, msg: str) -> None:
        self._pending_println.append(("error", msg))

    def log_text(self) -> str:
        # /copy all：屏幕上的纯文本（LogView 自己维护 strips，不含样式）。
        try:
            return self.query_one("#log", LogView).plain_text()
        except Exception:  # noqa: BLE001 - not mounted yet
            return ""

    def flush_prints(self) -> None:
        pending = self._pending_println
        self._pending_println = []
        if not pending:
            return
        try:
            log = self.query_one("#log", LogView)
        except Exception:  # noqa: BLE001 - not mounted yet
            return
        for kind, msg in pending:
            log.write(error_block(msg) if kind == "error" else Text(msg))

    # ── UI: queries ──────────────────────────────────────────────────────

    def mode(self) -> Mode:
        return self._mode

    def set_mode(self, m: Mode) -> None:
        self._mode = m
        try:
            self._update_statusbar()
        except Exception:  # noqa: BLE001
            pass

    def usage_in(self) -> int:
        return self._usage_in

    def usage_out(self) -> int:
        return self._usage_out

    def model_name(self) -> str:
        return self.provider.model if self.provider is not None else ""

    def cwd(self) -> str:
        return self.workspace

    def tool_count(self) -> int:
        return self._tool_registry.count()

    def memory_files(self) -> list[str]:
        if self.mem_mgr is None:
            return []
        project, user = self.mem_mgr.list_files()
        return [f"project: {name}" for name in project] + [f"user: {name}" for name in user]

    def session_path(self) -> str:
        return self.writer.path if self.writer is not None else ""

    def session_id(self) -> str:
        ctx = self.runtime.session
        return ctx.session_id if ctx is not None else ""

    def list_catalog_skills(self) -> list[SkillSummary]:
        return [
            SkillSummary(
                name=skill.name,
                description=skill.description,
                source=skill.source.value,
                mode=skill.mode,
            )
            for skill in self.skill_loader.list()
        ]

    def list_active_skills(self) -> list[str]:
        return self.runtime.active_skills.names()

    def hook_summaries(self) -> list[HookSummary]:
        engine = getattr(self, "hook_engine", None)
        if engine is None:
            return []
        return [
            HookSummary(
                name=rule.name,
                event=rule.event.value,
                action_type=rule.action.type.value,
                only_once=rule.only_once,
                async_mode=rule.asyncio_mode,
                source=rule.source,
            )
            for rule in engine.rules
        ]

    def hook_sources(self) -> list[str]:
        engine = getattr(self, "hook_engine", None)
        return engine.sources if engine is not None else []

    def clear_active_skills(self) -> None:
        self.runtime.active_skills.clear()

    def append_assistant_message(self, text: str) -> None:
        # fork 结果回流：写入主对话（含 JSONL）并显示在 scrollback。
        if not text:
            return
        self.conv.add_assistant(text)
        try:
            self.query_one("#log", LogView).write(render_markdown(text))
        except Exception:  # noqa: BLE001 - not mounted yet
            pass

    def conversation_messages(self) -> list[Message]:
        return self.conv.messages()

    def command_args(self) -> str:
        return self._command_args

    def set_command_args(self, args: str) -> None:
        self._command_args = args

    def idle(self) -> bool:
        from lcode.tui.app import SessionState

        return self.state is SessionState.IDLE

    # ── UI: actions ──────────────────────────────────────────────────────

    def quit(self) -> None:
        asyncio.create_task(self.action_quit())

    def force_compact(self) -> None:
        if self.compact_fn is None:
            self.error("上下文压缩尚未启用")
            self.flush_prints()
            return
        self._compact_task = asyncio.create_task(self._run_force_compact())

    async def _run_force_compact(self, reason: str = "manual") -> None:
        history = self.conv.messages()
        await self._dispatch_hook_event(
            HookEvent.PRE_COMPACT, reason=reason, message_count=len(history)
        )
        try:
            msgs = await self.compact_fn(history)
        except Exception as exc:  # noqa: BLE001 - report, never crash the TUI
            self.error(f"压缩失败: {exc}")
            self.flush_prints()
            return
        if msgs is None:
            self.error("压缩失败: 未返回结果")
            self.flush_prints()
            return
        self.conv.replace_history(msgs)
        await self._dispatch_hook_event(
            HookEvent.POST_COMPACT, reason=reason, message_count=len(msgs)
        )
        self._redraw_conversation()
        self.println("已压缩上下文")
        self.flush_prints()

    def open_resume_menu(self) -> None:
        from lcode.tui.resume import begin_resume

        begin_resume(self)

    def view_image(self, path: str, question: str) -> None:
        # 视觉请求要几秒到几十秒：放后台任务，界面先给一行反馈，不卡住输入。
        self._image_task = asyncio.create_task(self._run_view_image(path, question))

    async def _run_view_image(self, path: str, question: str) -> None:
        from lcode.tool.view_image import VISION_SETUP_HINT

        tool = self._tool_registry.get("view_image")
        if tool is None:
            self.error(VISION_SETUP_HINT)
            self.flush_prints()
            return
        self.println(f"正在看图：{path}")
        self.flush_prints()
        try:
            result = await tool.execute(json.dumps({"paths": [path], "question": question}))
        except Exception as exc:  # noqa: BLE001 - 工具失败不能打断会话
            self.error(f"看图失败: {exc}")
            self.flush_prints()
            return
        if result.is_error:
            self.error(result.content)
            self.flush_prints()
            return
        # 答案作为一条用户消息进对话：历史里只有文字，JSONL/压缩都不用改。
        prompt = (
            f"（用户用 /image 把图片 {path} 交给视觉模型看过，下面是它对"
            f"「{question}」的回答。需要更多细节时可以再调用 view_image，"
            f"用更具体的问题或 crop 放大局部。）\n\n{result.content}"
        )
        self.inject_and_send(f"/image {path}", prompt)

    def transcribe_audio(self, path: str, prompt: str) -> None:
        # 转写可能跑几分钟：同样放后台，界面立即返回。
        self._audio_task = asyncio.create_task(self._run_transcribe_audio(path, prompt))

    async def _run_transcribe_audio(self, path: str, prompt: str) -> None:
        from lcode.tool.transcribe_audio import AUDIO_SETUP_HINT

        tool = self._tool_registry.get("transcribe_audio")
        if tool is None:
            self.error(AUDIO_SETUP_HINT)
            self.flush_prints()
            return
        self.println(f"正在转写：{path}")
        self.flush_prints()
        args: dict = {"path": path}
        if prompt:
            args["prompt"] = prompt
        try:
            result = await tool.execute(json.dumps(args))
        except Exception as exc:  # noqa: BLE001 - 工具失败不能打断会话
            self.error(f"转写失败: {exc}")
            self.flush_prints()
            return
        if result.is_error:
            self.error(result.content)
            self.flush_prints()
            return
        note = (
            f"（用户用 /audio 转写了 {path}，下面是带时间戳的转写文本。"
            f"需要核对某句话时可以按时间戳再问。）"
        )
        if prompt:
            note = f"（用户的提示：{prompt}）\n{note}"
        self.inject_and_send(f"/audio {path}", f"{note}\n\n{result.content}")

    async def clear_and_new_session(self) -> None:
        # 旧会话先收尾，再重置 runtime（清 only_once/reminder），最后启动新会话。
        await self._dispatch_hook_event(HookEvent.SESSION_END, reason="clear")
        try:
            ctx = new_session_context(self.workspace)
            new_writer = open_writer(ctx.session_dir)
        except Exception as exc:  # noqa: BLE001 - keep the old session usable
            self.error(f"新建会话失败: {exc}")
            return

        if self.provider is not None:
            new_writer.model = self.provider.model

        old_writer = self.writer
        self.writer = new_writer
        self.ses_ctx = ctx
        self.runtime.reset_for_new_session(ctx)
        self.conv = self._bind_conversation(new_writer)
        self.iter = 0
        self._usage_in = 0
        self._usage_out = 0
        if old_writer is not None:
            try:
                old_writer.close()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass
        self._clear_log()
        await self._dispatch_hook_event(HookEvent.SESSION_START, source="clear")

    def inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        self.conv.add_user(preset_prompt)
        try:
            self.query_one("#log", LogView).write(user_block(display_label))
        except Exception:  # noqa: BLE001
            pass
        self._begin_turn()

    # ── dispatcher ───────────────────────────────────────────────────────

    async def dispatch_slash(self, text: str) -> bool:
        # 返回 True 表示输入已按斜杠命令处理，不应再送给 Agent。
        name, args, is_slash = parse_with_args(text)
        if not is_slash:
            return False

        self._pending_println = []
        command = self.cmd_registry.lookup(name) if name else None
        if command is None or (args and not command.accepts_args):
            shown = f": /{name}" if name else ""
            self.println(UNKNOWN_COMMAND_HINT.format(name=shown))
        elif command.kind in (Kind.UI, Kind.PROMPT) and not self.idle():
            self.error("请等待当前任务完成")
        else:
            self.set_command_args(args)
            try:
                await command.handler(self)
            except Exception as exc:  # noqa: BLE001 - surface handler failures to the user
                self.error(str(exc))
            finally:
                self.set_command_args("")

        self.flush_prints()
        return True

    # ── helpers ──────────────────────────────────────────────────────────

    async def _dispatch_hook_event(self, event: HookEvent, **extra) -> DispatchResult:
        # TUI 侧事件（会话/压缩/退出）统一入口；prompt 动作进入 reminder 队列。
        engine = getattr(self, "hook_engine", None)
        if engine is None:
            return DispatchResult()
        session = self.runtime.session
        payload: dict = {
            "event": event.value,
            "session_id": session.session_id if session is not None else "",
            "cwd": self.workspace,
            "mode": str(self._mode),
        }
        payload.update(extra)
        result = await engine.dispatch(event, payload)
        self.runtime.append_reminders(result.injected_prompts)
        return result

    def _bind_conversation(self, writer) -> Conversation:
        # 启动与 /clear 共用：会话持久化回调始终跟随当前 writer。
        if writer is None:
            return Conversation()
        return Conversation(on_append=writer.on_append, on_replace=writer.on_replace)

    def _begin_turn(self) -> None:
        from lcode.tui.app import SessionState

        self.cur_reply = ""
        self.cur_tools = []
        self.iter = 0
        self.turn_start = time.monotonic()
        self.state = SessionState.STREAMING
        self.turn_cancel = asyncio.Event()
        self._stream_task = asyncio.create_task(self._consume_events())
        self._timer = self.set_interval(0.1, self._tick)

    def _clear_log(self) -> None:
        try:
            log = self.query_one("#log", LogView)
            log.clear()
            log.write(Text(render_banner(self._version, self.workspace)))
            self._update_statusbar()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["UNKNOWN_COMMAND_HINT", "CommandMixin"]
