"""Coordinator Mode: tool allowlist and prompt discipline (feature + env double lock)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.config import Config

COORDINATOR_ALLOWED_TOOLS: list[str] = [
    "Agent",
    "TeamCreate",
    "TeamDelete",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
    "read_file",
    "glob",
    "grep",
    "bash",
]

SYSTEM_PROMPT_SUFFIX = """

## Coordinator Mode

你正处于 Coordinator 模式，专注于研究、综合、实施、验证四阶段调度。
- 派出 Agent / SendMessage 后立即停手等待汇报；禁止立刻用 read_file/glob/grep/bash 自己探索；
  禁止用 sleep 或 TaskList 轮询凑时间。
- 队员完成后系统会以 reminder 推送结果；被唤醒后再继续。
- 允许自己用只读工具的场景仅限：Research 第一次定位目标、Synthesis 阅读队员报告、
  Verification 执行 git diff / git status 等收敛操作。
- 你不能再使用 write_file / edit_file；需要写文件时派队员或使用 bash。
"""


def env_truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def is_enabled(cfg: Config) -> bool:
    features = getattr(cfg, "features", None)
    if features is None or not getattr(features, "coordinator_mode", False):
        return False
    return env_truthy(os.environ.get("MEWCODE_COORDINATOR_MODE", ""))


def allowed_tools() -> list[str]:
    return list(COORDINATOR_ALLOWED_TOOLS)


def system_prompt_suffix() -> str:
    return SYSTEM_PROMPT_SUFFIX


__all__ = [
    "COORDINATOR_ALLOWED_TOOLS",
    "SYSTEM_PROMPT_SUFFIX",
    "allowed_tools",
    "env_truthy",
    "is_enabled",
    "system_prompt_suffix",
]
