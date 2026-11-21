"""Execute skills inline (main conversation) or in an isolated fork."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from lcode.compact.summary_prompt import serialize_conversation
from lcode.conversation import Conversation
from lcode.llm import ROLE_ASSISTANT, ROLE_USER, Message, Provider
from lcode.skills.loader import SkillLoader
from lcode.skills.parser import SkillDef
from lcode.skills.render import render_body

if TYPE_CHECKING:
    from lcode.agent.runtime import SessionRuntime
    from lcode.command.ui import UI
    from lcode.permission.engine import Engine
    from lcode.tool import Registry

logger = logging.getLogger(__name__)

# 不经过普通工具权限弹窗的系统工具名（LoadSkill 是 read-only）。
SYSTEM_TOOL_NAMES = frozenset({"LoadSkill"})

MAX_SUMMARY_CHARS = 4000


class SkillExecutor:
    # inline：把渲染后的 SOP 钉到 runtime，再触发一轮主对话。
    # fork：构造独立 Conversation + SessionRuntime，子 Agent 跑完后把文本回流主对话。
    def __init__(
        self,
        loader: SkillLoader,
        runtime: SessionRuntime,
        registry: Registry | None = None,
        engine: Engine | None = None,
        version: str = "",
        provider: Provider | None = None,
        agent=None,
    ) -> None:
        self._loader = loader
        self._runtime = runtime
        self._registry = registry
        self._engine = engine
        self._version = version
        self._provider = provider
        # agent 是可选的 activate_skill 门面；缺省时直接写 runtime.active_skills。
        self._agent = agent
        self._tasks: set[asyncio.Task] = set()

    def set_provider(self, provider: Provider | None) -> None:
        self._provider = provider

    # ── entry points ─────────────────────────────────────────────────────

    async def execute(self, ui: UI, name: str, args: str = "") -> None:
        skill = self._loader.get(name)
        if skill is None:
            ui.error(f"未知 Skill: {name}")
            return
        if skill.is_fork:
            task = asyncio.create_task(self._fork_and_report(ui, skill, args))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return
        await self.execute_inline(skill, args, ui)

    async def execute_inline(self, skill: SkillDef, args: str = "", ui: UI | None = None) -> None:
        # F6：渲染 → activate_skill 钉 SOP → 交由调用方触发主循环。
        body = render_body(skill, args)
        self._activate(skill.name, body)
        if ui is not None:
            ui.inject_and_send(f"/{skill.name}", body)

    async def execute_fork(
        self,
        skill: SkillDef,
        args: str = "",
        ui: UI | None = None,
        main_messages: list[Message] | None = None,
    ) -> str:
        if self._provider is None:
            return f"[skill {skill.name} failed: provider 未就绪]"
        try:
            body = render_body(skill, args)
            history = main_messages
            if history is None and ui is not None:
                history = ui.conversation_messages()
            fork_conv = self._build_fork_conversation(history or [], skill.context, body)
            fork_runtime = self._new_fork_runtime()
            # 局部 import：避免 runtime → skills 包 → agent 的循环依赖。
            from lcode.agent.launch import ForkLaunchOpts, launch_fork

            text = await launch_fork(
                ForkLaunchOpts(
                    conv=fork_conv,
                    provider=self._provider_for(skill),
                    registry=self._registry,
                    version=self._version,
                    engine=self._engine,
                    runtime=fork_runtime,
                    hook_engine=self._runtime.hook_engine,
                )
            )
            tail = _last_assistant(fork_conv)
            return tail or text or f"[skill {skill.name} 未产生输出]"
        except asyncio.CancelledError:
            return f"[skill {skill.name} cancelled]"
        except Exception as exc:  # noqa: BLE001 - fork failure must not break the TUI
            logger.exception("fork skill 执行失败: %s", skill.name)
            return f"[skill {skill.name} failed: {exc}]"

    # ── internals ────────────────────────────────────────────────────────

    async def _fork_and_report(self, ui: UI, skill: SkillDef, args: str) -> None:
        text = await self.execute_fork(skill, args, ui)
        ui.append_assistant_message(text)

    def _activate(self, name: str, body: str) -> None:
        if self._agent is not None and hasattr(self._agent, "activate_skill"):
            self._agent.activate_skill(name, body)
            return
        self._runtime.active_skills.activate(name, body)

    def _new_fork_runtime(self) -> SessionRuntime:
        from lcode.agent.runtime import SessionRuntime

        return SessionRuntime(
            instruction_text=self._runtime.instruction_text,
            memory_text=self._runtime.memory_text,
            session=self._runtime.session,
            context_window=self._runtime.context_window,
            skill_catalog=self._loader,
        )

    def _build_fork_conversation(
        self, history: list[Message], context: str, body: str
    ) -> Conversation:
        # none：完全隔离；recent：最近 5 条 user/assistant；full：摘要一段。
        initial: list[Message] = []
        if context == "recent":
            initial = [m for m in history if m.role in (ROLE_USER, ROLE_ASSISTANT)][-5:]
        elif context == "full":
            summary = _summarize(history)
            if summary:
                initial = [
                    Message(
                        role=ROLE_USER,
                        content="## Previous conversation summary\n\n" + summary,
                    )
                ]
        conv = Conversation.from_messages(initial)
        conv.add_user(body)
        return conv

    def _provider_for(self, skill: SkillDef) -> Provider:
        # skill.model 非空且 provider 支持克隆时，用同一配置换模型。
        if skill.model and hasattr(self._provider, "with_model"):
            try:
                return self._provider.with_model(skill.model)
            except Exception as exc:  # noqa: BLE001 - fall back to the session provider
                logger.warning("无法为 skill '%s' 切换模型 %s: %s", skill.name, skill.model, exc)
        return self._provider

    def cancel_pending(self) -> None:
        for task in list(self._tasks):
            task.cancel()


def _summarize(history: list[Message]) -> str:
    text = serialize_conversation(history).strip()
    if not text:
        return ""
    if len(text) > MAX_SUMMARY_CHARS:
        text = "..." + text[-MAX_SUMMARY_CHARS:]
    return text


def _last_assistant(conv: Conversation) -> str:
    for message in reversed(conv.messages()):
        if message.role == ROLE_ASSISTANT and message.content.strip():
            return message.content
    return ""


__all__ = ["SYSTEM_TOOL_NAMES", "SkillExecutor"]
