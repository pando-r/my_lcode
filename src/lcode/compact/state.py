"""Session-level state: replacement ledger, circuit breaker, file recovery, session id."""

# 定义跨轮次必须记住的东西
from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lcode.compact.const import MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


class ContentReplacementState:
    """Freeze tool-result replacement decisions so prompt-cache prefixes stay byte-stable."""

    """工具替不替，决策必须冻结（同一 tool_call_id 只决策一次），否则缓存前缀抖动"""

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()  # 已决策过的 id
        self._replacements: dict[str, str] = {}  # # id → 替换后的预览文本

    # 决策函数，返回决策和预览文本
    def decide_once(
        self,
        tool_use_id: str,
        original_content: str,
        decide: Callable[[], tuple[str, str]],
    ) -> str:
        """Read ledger → decide → write ledger, atomically (single event loop)."""
        if tool_use_id in self._seen_ids:
            return self._replacements.get(tool_use_id, original_content)
        decision, preview = decide()
        if decision == "kept":
            self._seen_ids.add(tool_use_id)
            return original_content
        if decision == "replaced":
            self._seen_ids.add(tool_use_id)
            self._replacements[tool_use_id] = preview
            return preview
        # "skip": write nothing; retried next round
        return original_content


class CompactCircuitBreaker:
    """Track consecutive auto-compaction failures (manual/emergency bypass this)."""

    """记住自动压缩连续失败了几次"""

    def __init__(self) -> None:
        self._consecutive_failures = 0

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1

    def tripped(self) -> bool:
        return self._consecutive_failures >= MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


@dataclass
class FileReadRecord:
    path: str
    content: str
    timestamp: datetime


class RecoveryState:
    """Tracks recently read files (keyed by absolute path)."""

    """记住读过哪些文件（供摘要后回填）、落盘目录在哪"""

    def __init__(self) -> None:
        self._files: dict[str, FileReadRecord] = {}

    def record_file(self, path: str, content: str) -> None:
        abs_path = path if Path(path).is_absolute() else str(Path(path).resolve())
        self._files[abs_path] = FileReadRecord(
            path=abs_path, content=content, timestamp=datetime.now()
        )

    def snapshot(self) -> list[FileReadRecord]:
        return sorted(self._files.values(), key=lambda r: r.timestamp, reverse=True)


@dataclass
class SessionContext:
    # session_id 形如 "20260601-143022-a1b2"。
    session_id: str
    # <workspace>/.lcode/sessions/<session_id>
    session_dir: str = ""
    # session_dir + "/tool-results"
    spill_dir: str = ""


_SESSION_ID_RE = re.compile(r"^(\d{8})-(\d{6})-([0-9a-f]{4})$")


def _new_session_id() -> str:
    # 格式：YYYYMMDD-HHMMSS-xxxx（本地时间 + 4 位随机十六进制防同秒碰撞）。
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        suffix = secrets.token_hex(2)
    except Exception:  # noqa: BLE001
        import random

        suffix = f"{random.Random(time.time()).randrange(0x10000):04x}"
    return f"{stamp}-{suffix}"


def parse_session_time(session_id: str) -> datetime:
    # 从新格式 session ID 解析启动时刻；旧格式（unix 时间戳开头）抛 ValueError。
    match = _SESSION_ID_RE.match(session_id)
    if match is None:
        raise ValueError(f"不是新格式 session id: {session_id!r}")
    return datetime.strptime(f"{match.group(1)}-{match.group(2)}", "%Y%m%d-%H%M%S")


def new_session_context(workspace: str) -> SessionContext:
    session_id = _new_session_id()
    session_dir = str(Path(workspace) / ".lcode" / "sessions" / session_id)
    spill_dir = str(Path(session_dir) / "tool-results")
    Path(spill_dir).mkdir(parents=True, exist_ok=True)
    return SessionContext(session_id=session_id, session_dir=session_dir, spill_dir=spill_dir)


def open_session_context(workspace: str, session_id: str) -> SessionContext:
    # 打开已有会话目录（恢复场景），不创建目录。
    session_dir = Path(workspace) / ".lcode" / "sessions" / session_id
    if not session_dir.is_dir():
        raise FileNotFoundError(f"会话不存在: {session_id}")
    return SessionContext(
        session_id=session_id,
        session_dir=str(session_dir),
        spill_dir=str(session_dir / "tool-results"),
    )
