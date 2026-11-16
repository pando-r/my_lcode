"""Note file CRUD and MEMORY.md index management for one level (F28-F31/F40)."""

# 它只负责一件事——把 UpdateAction 安全、一致地写入磁盘，并维护 MEMORY.md 索引。
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

import yaml

from mewcode.memory.types import NoteType, UpdateAction

logger = logging.getLogger(__name__)

INDEX_NAME = "MEMORY.md"
MAX_DESCRIPTION = 120
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class Store:
    # 管理单级（项目级或用户级）的笔记文件和索引。
    def __init__(self, directory: str) -> None:
        self._dir = directory
        self._lock = threading.Lock()

    @property
    def dir(self) -> str:
        return self._dir

    def ensure_dir(self) -> None:
        os.makedirs(self._dir, exist_ok=True)

    def load_index(self) -> str:
        path = Path(self._dir) / INDEX_NAME
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def list_files(self) -> list[str]:
        # 仅列出本层目录下的 .md 文件名（含 MEMORY.md），按字典序；目录缺失视为空。
        try:
            names = [
                path.name
                for path in Path(self._dir).iterdir()
                if path.is_file() and path.suffix == ".md"
            ]
        except FileNotFoundError:
            return []
        except OSError:
            logger.warning("读取记忆目录失败: %s", self._dir, exc_info=True)
            return []
        return sorted(names)

    def apply(self, actions: list[UpdateAction]) -> None:
        if not actions:
            return
        with self._lock:
            self.ensure_dir()
            lines = self.load_index().splitlines()
            for action in actions:
                try:
                    self._apply_one(action, lines)
                except Exception:  # noqa: BLE001 - one bad note must not abort the batch
                    logger.exception("应用记忆操作失败: %r", action)
            self._write_index(lines)

    # ── operations ───────────────────────────────────────────────────────

    def _apply_one(self, action: UpdateAction, lines: list[str]) -> None:
        if action.action == "create":
            self._create(action, lines)
        elif action.action == "update":
            self._update(action, lines)
        elif action.action == "delete":
            self._delete(action, lines)

    def _create(self, action: UpdateAction, lines: list[str]) -> None:
        note_type = _note_type(action.type)
        if note_type is None:
            logger.warning("跳过 create：未知笔记类型 %r", action.type)
            return
        filename = _safe_filename(action.filename)
        if filename is None:
            filename = _filename_for(note_type, action.slug or action.title)
        title = action.title or action.slug or "untitled"

        now = _now()
        meta = {
            "type": note_type.value,
            "title": title,
            "created": now.isoformat(),
            "updated": now.isoformat(),
        }
        _write_note(Path(self._dir) / filename, meta, action.content)
        _replace_line(
            lines,
            _index_prefix(note_type.value, title),
            _index_line(note_type.value, title, action.content),
        )

    def _update(self, action: UpdateAction, lines: list[str]) -> None:
        filename = _safe_filename(action.filename)
        if filename is None:
            logger.warning("跳过 update：非法文件名 %r", action.filename)
            return
        path = Path(self._dir) / filename
        old_meta = _read_frontmatter(path)
        if not path.is_file():
            logger.warning("跳过 update：笔记不存在 %s", path)
            return

        old_type = str(old_meta.get("type", ""))
        old_title = str(old_meta.get("title", ""))
        new_type = _note_type(action.type) or _note_type(old_type)
        if new_type is None:
            logger.warning("跳过 update：未知笔记类型 %r", action.type or old_type)
            return
        new_title = action.title or old_title or action.slug or "untitled"

        now = _now()
        meta = {
            "type": new_type.value,
            "title": new_title,
            "created": str(old_meta.get("created") or now.isoformat()),
            "updated": now.isoformat(),
        }
        _write_note(path, meta, action.content)

        if old_type and old_title:
            _remove_line(lines, _index_prefix(old_type, old_title))
        _replace_line(
            lines,
            _index_prefix(new_type.value, new_title),
            _index_line(new_type.value, new_title, action.content),
        )

    def _delete(self, action: UpdateAction, lines: list[str]) -> None:
        filename = _safe_filename(action.filename)
        if filename is None:
            logger.warning("跳过 delete：非法文件名 %r", action.filename)
            return
        path = Path(self._dir) / filename
        meta = _read_frontmatter(path)
        note_type = str(meta.get("type", "")) or action.type
        title = str(meta.get("title", "")) or action.title
        if note_type and title:
            _remove_line(lines, _index_prefix(note_type, title))
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    # ── index ────────────────────────────────────────────────────────────

    def _write_index(self, lines: list[str]) -> None:
        path = Path(self._dir) / INDEX_NAME
        text = "\n".join(lines)
        if text:
            text += "\n"
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:
            logger.exception("写入记忆索引失败: %s", path)


# ── helpers ──────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now().astimezone()


def _note_type(value: str) -> NoteType | None:
    try:
        return NoteType(value)
    except ValueError:
        return None


def _safe_filename(raw: str) -> str | None:
    raw = raw.strip()
    if not raw or raw in (".", ".."):
        return None
    if "/" in raw or "\\" in raw:
        return None
    if not raw.endswith(".md"):
        raw += ".md"
    return raw


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("_", text.lower()).strip("_")
    return slug[:60] or "note"


def _filename_for(note_type: NoteType, slug_or_title: str) -> str:
    return f"{note_type.value}_{_slugify(slug_or_title)}.md"


def _index_prefix(note_type: str, title: str) -> str:
    return f"- [{note_type}] {title} — "


def _index_line(note_type: str, title: str, content: str) -> str:
    description = _first_line(content) or title
    return _index_prefix(note_type, title) + description


def _first_line(content: str) -> str:
    for line in content.splitlines():
        line = " ".join(line.split())
        if line:
            return line[:MAX_DESCRIPTION]
    return ""


def _replace_line(lines: list[str], prefix: str, new_line: str) -> None:
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = new_line
            return
    lines.append(new_line)


def _remove_line(lines: list[str], prefix: str) -> None:
    lines[:] = [line for line in lines if not line.startswith(prefix)]


def _write_note(path: Path, meta: dict, content: str) -> None:
    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    body = content.rstrip()
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")


def _read_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            end = index
            break
    if end is None:
        return {}
    try:
        meta = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


__all__ = ["INDEX_NAME", "Store"]
