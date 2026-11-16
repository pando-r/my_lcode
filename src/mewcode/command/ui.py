"""UI abstraction: the only channel command handlers may use to reach the TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mewcode.llm import Message
from mewcode.permission import Mode


@dataclass(frozen=True)
class SkillSummary:
    # /skill 与 UI 查询用的只读摘要；不把 skills 包类型泄漏到 command 层。
    name: str
    description: str
    source: str
    mode: str


@dataclass(frozen=True)
class WorktreeSummary:
    name: str
    path: str
    branch: str
    active: bool
    manual: bool


class WorktreeAccessor(Protocol):
    async def create(self, name: str) -> tuple[str, str]: ...
    def list(self) -> list[WorktreeSummary]: ...
    async def enter(self, name: str) -> None: ...
    async def exit(self, action: str, discard: bool) -> bool: ...
    async def remove(self, name: str, discard: bool) -> None: ...


@dataclass(frozen=True)
class TeamSummary:
    name: str
    backend: str
    member_count: int
    active_count: int
    config_path: str = ""


@dataclass(frozen=True)
class TeamMemberSummary:
    name: str
    agent_id: str
    backend: str
    worktree_path: str
    is_active: bool | None
    task_count: int = 0


class TeamAccessor(Protocol):
    def list(self) -> list[TeamSummary]: ...
    async def info(self, name: str) -> tuple[TeamSummary, list[TeamMemberSummary]]: ...
    async def delete(self, name: str, force: bool) -> None: ...
    async def kill(self, member: str) -> str: ...


@dataclass(frozen=True)
class HookSummary:
    # /hooks 与 UI 查询用的只读摘要；不把 hook.Rule 泄漏到 command 层。
    name: str
    event: str
    action_type: str
    only_once: bool = False
    async_mode: bool = False
    source: str = ""


class UI(Protocol):
    # 输出
    def println(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...

    # 权限模式
    def mode(self) -> Mode: ...
    def set_mode(self, m: Mode) -> None: ...

    # 对话注入：label 显示在 scrollback，preset 写入 conversation/JSONL
    def inject_and_send(self, display_label: str, preset_prompt: str) -> None: ...

    # 只读查询
    def usage_in(self) -> int: ...
    def usage_out(self) -> int: ...
    def model_name(self) -> str: ...
    def cwd(self) -> str: ...
    def tool_count(self) -> int: ...
    def memory_files(self) -> list[str]: ...
    def session_path(self) -> str: ...
    def session_id(self) -> str: ...

    # 影响界面动作
    def quit(self) -> None: ...
    def force_compact(self) -> None: ...
    def open_resume_menu(self) -> None: ...
    async def clear_and_new_session(self) -> None: ...

    # Hook（F? /hooks）
    def hook_summaries(self) -> list[HookSummary]: ...
    def hook_sources(self) -> list[str]: ...

    # Worktree（ch14）
    def worktree_accessor(self) -> WorktreeAccessor | None: ...

    # Team（ch15）
    def team_accessor(self) -> TeamAccessor | None: ...

    # Skill（F12/F13/F18）
    def list_catalog_skills(self) -> list[SkillSummary]: ...
    def list_active_skills(self) -> list[str]: ...
    def clear_active_skills(self) -> None: ...
    def append_assistant_message(self, text: str) -> None: ...
    def conversation_messages(self) -> list[Message]: ...

    # 当前命令参数（仅 accepts_args=True 的命令会收到非空值）
    def command_args(self) -> str: ...
    def set_command_args(self, args: str) -> None: ...

    # 状态机查询
    def idle(self) -> bool: ...


class NopUI:
    # 测试桩：所有写入 no-op，所有查询返回零值。

    def __init__(self) -> None:
        self.printed: list[str] = []
        self.errors: list[str] = []
        self.mode_value: Mode = Mode.DEFAULT
        self.injected: list[tuple[str, str]] = []
        self._memory_files: list[str] = []
        self._session_path = ""
        self._session_id = ""
        self._idle = True
        self._hook_summaries: list[HookSummary] = []
        self._hook_sources: list[str] = []
        self._catalog_skills: list[SkillSummary] = []
        self._active_skills: list[str] = []
        self._appended: list[str] = []
        self._history: list[Message] = []
        self._command_args = ""

    def println(self, msg: str) -> None:
        self.printed.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def mode(self) -> Mode:
        return self.mode_value

    def set_mode(self, m: Mode) -> None:
        self.mode_value = m

    def inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        self.injected.append((display_label, preset_prompt))

    def usage_in(self) -> int:
        return 0

    def usage_out(self) -> int:
        return 0

    def model_name(self) -> str:
        return ""

    def cwd(self) -> str:
        return ""

    def tool_count(self) -> int:
        return 0

    def memory_files(self) -> list[str]:
        return list(self._memory_files)

    def session_path(self) -> str:
        return self._session_path

    def session_id(self) -> str:
        return self._session_id

    def quit(self) -> None:
        pass

    def force_compact(self) -> None:
        pass

    def open_resume_menu(self) -> None:
        pass

    async def clear_and_new_session(self) -> None:
        pass

    def hook_summaries(self) -> list[HookSummary]:
        return list(self._hook_summaries)

    def hook_sources(self) -> list[str]:
        return list(self._hook_sources)

    def worktree_accessor(self) -> WorktreeAccessor | None:
        return None

    def team_accessor(self) -> TeamAccessor | None:
        return None

    def list_catalog_skills(self) -> list[SkillSummary]:
        return list(self._catalog_skills)

    def list_active_skills(self) -> list[str]:
        return list(self._active_skills)

    def clear_active_skills(self) -> None:
        self._active_skills.clear()

    def append_assistant_message(self, text: str) -> None:
        self._appended.append(text)

    def conversation_messages(self) -> list[Message]:
        return list(self._history)

    def command_args(self) -> str:
        return self._command_args

    def set_command_args(self, args: str) -> None:
        self._command_args = args

    def idle(self) -> bool:
        return self._idle
