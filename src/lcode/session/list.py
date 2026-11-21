"""Scan saved sessions for the /resume picker (F18/F20)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lcode.compact.state import parse_session_time
from lcode.session.writer import JSONL_NAME

logger = logging.getLogger(__name__)

TITLE_LIMIT = 50


@dataclass
class SessionInfo:
    id: str
    title: str
    modified_at: datetime
    model: str
    size: int
    dir: str


def list_sessions(sessions_dir: str) -> list[SessionInfo]:
    # 只返回包含 conversation.jsonl 且 ID 能解析为新格式的目录。
    root = Path(sessions_dir)
    if not root.is_dir():
        return []

    items: list[SessionInfo] = []
    try:
        children = list(root.iterdir())
    except OSError:
        logger.warning("扫描会话目录失败: %s", root)
        return []

    for child in children:
        if not child.is_dir():
            continue
        try:
            parse_session_time(child.name)
        except ValueError:
            continue  # 旧格式目录不展示（N3）

        jsonl = child / JSONL_NAME
        if not jsonl.is_file():
            continue

        title, model = _read_title_and_model(jsonl)
        if not title:
            title = "（无标题）"
        try:
            stat = jsonl.stat()
        except OSError:
            continue

        items.append(
            SessionInfo(
                id=child.name,
                title=title,
                modified_at=datetime.fromtimestamp(stat.st_mtime),
                model=model,
                size=stat.st_size,
                dir=str(child),
            )
        )

    items.sort(key=lambda item: item.modified_at, reverse=True)
    return items


def _read_title_and_model(path: Path) -> tuple[str, str]:
    title = ""
    model = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                if not model and isinstance(data.get("model"), str):
                    model = data["model"]
                if data.get("role") == "user" and not title:
                    title = _truncate_title(str(data.get("content", "")))
                if title and model:
                    break
    except OSError:
        logger.warning("读取会话文件失败: %s", path)
    return title, model


def _truncate_title(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= TITLE_LIMIT:
        return text
    return text[: TITLE_LIMIT - 1] + "…"


def size_label(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def relative_time(moment: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


__all__ = ["SessionInfo", "list_sessions", "relative_time", "size_label"]
