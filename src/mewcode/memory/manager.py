"""Coordinate project/user memory stores and asynchronous LLM updates (F32-F42)."""

# 编排层
from __future__ import annotations

import asyncio
import json
import logging
import re

from mewcode.llm import ROLE_USER, Message, Provider, Request, System
from mewcode.memory.prompts import MEMORY_UPDATE_SYSTEM_PROMPT, build_update_prompt
from mewcode.memory.store import Store
from mewcode.memory.types import UpdateAction

logger = logging.getLogger(__name__)

MAX_INDEX_BYTES = 25 * 1024
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?|\n?```\s*$")
_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


class Manager:
    # 编排项目级和用户级笔记的加载和更新。
    def __init__(
        self,
        project_dir: str,
        user_dir: str,
        provider: Provider | None = None,
        model: str = "",
    ) -> None:
        self._project = Store(project_dir)
        self._user = Store(user_dir)
        self._provider = provider
        self._model = model
        self._lock = asyncio.Lock()

    # ── read path ────────────────────────────────────────────────────────

    def load_index(self) -> str:
        # 项目级在前、用户级在后；合并后超过 25KB 截断。
        parts = []
        for store in (self._project, self._user):
            text = store.load_index().strip()
            if text:
                parts.append(text)
        merged = "\n\n".join(parts)
        data = merged.encode("utf-8")
        if len(data) > MAX_INDEX_BYTES:
            merged = data[:MAX_INDEX_BYTES].decode("utf-8", "ignore").rstrip()
            merged += "\n(index truncated)"
        return merged

    def list_files(self) -> tuple[list[str], list[str]]:
        # 返回 (项目层 .md 文件, 用户层 .md 文件)，各自已按文件名排序。
        return self._project.list_files(), self._user.list_files()

    def set_provider(self, provider: Provider | None, model: str = "") -> None:
        # provider 在 TUI 里选定后延迟注入。
        self._provider = provider
        self._model = model

    # ── write path ───────────────────────────────────────────────────────

    async def update_async(self, recent_msgs: list[Message]) -> None:
        # 异步、失败静默、不上抛（F42）。并发更新由 asyncio.Lock 串行化。
        if self._provider is None:
            return
        async with self._lock:
            try:
                prompt = build_update_prompt(
                    self._project.load_index(),
                    self._user.load_index(),
                    recent_msgs,
                )
                request = Request(
                    messages=[Message(role=ROLE_USER, content=prompt)],
                    system=System(stable=MEMORY_UPDATE_SYSTEM_PROMPT),
                )
                raw: list[str] = []
                async for event in self._provider.stream(request):
                    if event.err is not None:
                        raise event.err
                    if event.text:
                        raw.append(event.text)
                actions = parse_actions("".join(raw))
                self._apply(actions)
            except Exception:  # noqa: BLE001 - memory updates never affect the main session
                logger.exception("记忆更新失败")

    def _apply(self, actions: list[UpdateAction]) -> None:
        if not actions:
            return
        project_actions = [action for action in actions if action.level == "project"]
        user_actions = [action for action in actions if action.level == "user"]
        if project_actions:
            self._project.apply(project_actions)
        if user_actions:
            self._user.apply(user_actions)


# ── parsing ──────────────────────────────────────────────────────────────


def parse_actions(raw: str) -> list[UpdateAction]:
    text = raw.strip()
    if not text:
        return []

    text = _strip_fences(text)
    data = _load_json(text)
    if data is None:
        match = _ARRAY_RE.search(text)
        if match is not None:
            data = _load_json(match.group(0))
    if not isinstance(data, list):
        logger.warning("记忆更新返回的不是 JSON 数组")
        return []

    actions: list[UpdateAction] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        action = UpdateAction.from_dict(item)
        if action is not None:
            actions.append(action)
    return actions


def _strip_fences(text: str) -> str:
    text = _FENCE_RE.sub("", text)
    return text.strip()


def _load_json(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


__all__ = ["MAX_INDEX_BYTES", "Manager", "parse_actions"]
