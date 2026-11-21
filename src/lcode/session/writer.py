"""Append-only JSONL conversation writer (F11-F16)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from lcode.llm import Message

logger = logging.getLogger(__name__)

JSONL_NAME = "conversation.jsonl"


@dataclass
class Entry:
    role: str = ""
    content: str = ""
    tool_calls: list[dict] | None = None
    tool_results: list[dict] | None = None
    ts: int = 0
    model: str | None = None
    type: str | None = None


class Writer:
    # 向 conversation.jsonl 追加写入；每次 append 后 flush + fsync。
    def __init__(self, session_dir: str) -> None:
        os.makedirs(session_dir, exist_ok=True)
        self._path = os.path.join(session_dir, JSONL_NAME)
        self._file = open(self._path, "ab")
        self._lock = threading.Lock()
        self._first_pending = os.path.getsize(self._path) == 0
        # 当前 provider 的模型名；由 TUI 在选定 provider 后设置。
        self.model = ""

    @classmethod
    def open_existing(cls, session_dir: str) -> Writer:
        # 打开已有会话（追加模式），不创建目录。
        path = os.path.join(session_dir, JSONL_NAME)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        writer = cls.__new__(cls)
        writer._path = path
        writer._file = open(path, "ab")
        writer._lock = threading.Lock()
        writer._first_pending = os.path.getsize(path) == 0
        writer.model = ""
        return writer

    @property
    def path(self) -> str:
        return self._path

    # ── writing ──────────────────────────────────────────────────────────

    def append(self, msg: Message, model: str = "", is_first: bool | None = None) -> None:
        # 未显式传 model 时用 Writer 上当前的 provider 模型名。
        model = model or self.model
        with self._lock:
            first = self._first_pending if is_first is None else is_first
            entry = Entry(
                role=msg.role,
                content=msg.content,
                tool_calls=[asdict(c) for c in msg.tool_calls] or None,
                tool_results=[asdict(r) for r in msg.tool_results] or None,
                ts=int(time.time()),
                model=model if first and model else None,
            )
            self._write(asdict(entry))
            if first:
                self._first_pending = False

    def append_all(self, msgs: list[Message]) -> None:
        for msg in msgs:
            self.append(msg, model="", is_first=False)

    def write_compact_marker(self) -> None:
        with self._lock:
            self._write({"type": "compact", "ts": int(time.time())})

    # ── Conversation callbacks ───────────────────────────────────────────

    def on_append(self, msg: Message) -> None:
        self.append(msg, model=self.model)

    def on_replace(self, msgs: list[Message]) -> None:
        self.write_compact_marker()
        self.append_all(msgs)

    # ── lifecycle ────────────────────────────────────────────────────────

    def close(self) -> None:
        with self._lock:
            if self._file.closed:
                return
            try:
                self._file.flush()
                os.fsync(self._file.fileno())
            except OSError:
                pass
            self._file.close()

    def __enter__(self) -> Writer:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── internal ─────────────────────────────────────────────────────────

    def _write(self, payload: dict[str, Any]) -> None:
        # 去掉 None / 空串 / 空列表，保持 JSONL 简洁（F11 字段是可选的）。
        clean = {k: v for k, v in payload.items() if v not in (None, "", [])}
        # role 与 ts 是必需字段，先写回，避免被上面的过滤误删。
        if "role" in payload:
            clean["role"] = payload["role"]
        clean["ts"] = payload.get("ts") or int(time.time())
        if payload.get("type"):
            clean["type"] = payload["type"]

        line = json.dumps(clean, ensure_ascii=False) + "\n"
        try:
            self._file.write(line.encode("utf-8"))
            self._file.flush()
            os.fsync(self._file.fileno())
        except OSError:
            # JSONL 写入失败记录日志但不中断对话（N5）。
            logger.exception("写入会话 JSONL 失败: %s", self._path)


def open_writer(session_dir: str) -> Writer:
    # 统一的新会话写入入口（/clear 与启动路径共用）。
    return Writer(session_dir)
