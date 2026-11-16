"""Session-level runtime state shared across per-turn Agent instances."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mewcode.compact.state import ContentReplacementState, RecoveryState, SessionContext
from mewcode.memory import Manager as MemoryManager
from mewcode.skills.active import ActiveSkills
from mewcode.skills.loader import SkillLoader

if TYPE_CHECKING:
    from mewcode.hook.engine import Engine


@dataclass
class SessionRuntime:
    # 跨轮次持久的会话状态。ch08 的上下文管理账本在此扩展。
    turn_count: int = 0
    memory_manager: MemoryManager | None = None
    instruction_text: str = ""
    memory_text: str = ""
    session: SessionContext | None = None
    replacement: ContentReplacementState | None = None
    recovery: RecoveryState | None = None
    auto_tracking: object | None = None
    context_window: int = 0
    usage_anchor: int = 0
    anchor_msg_len: int = 0
    # Skill 激活态与 catalog 跨轮共享；Agent 每轮重建也能看到。
    active_skills: ActiveSkills = field(default_factory=ActiveSkills)
    skill_catalog: SkillLoader | None = None
    # Hook 注入的 reminder 只在下一轮 LLM 请求有效；only_once 状态由 Engine 持有。
    hook_engine: Engine | None = None
    pending_reminders: list[str] = field(default_factory=list)
    _reminder_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    def append_reminders(self, prompts: list[str]) -> None:
        if not prompts:
            return
        with self._reminder_lock:
            self.pending_reminders.extend(prompts)

    def take_reminders(self) -> list[str]:
        with self._reminder_lock:
            pending = self.pending_reminders
            self.pending_reminders = []
        return pending

    def reset_for_new_session(self, ses_ctx: SessionContext) -> None:
        # /clear：原子重置会话内累计状态；context_window 跟随 provider，不重置。
        self.session = ses_ctx
        self.replacement = None
        self.recovery = None
        self.auto_tracking = None
        self.usage_anchor = 0
        self.anchor_msg_len = 0
        self.turn_count = 0
        # /clear 后旧 SOP 不得继续注入 environment。
        self.active_skills.clear()
        self.take_reminders()
        if self.hook_engine is not None:
            self.hook_engine.reset_for_new_session()
