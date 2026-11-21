"""Approval escalation hook for sub-agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from lcode.permission import Outcome

if TYPE_CHECKING:
    from lcode.agent import ApprovalRequest

# 子 Agent 遇到 ASK 时把请求升级到父 TUI。
# 返回 (outcome, ok)；ok=False 表示升级不可用，调用方回退到默认 Approval 事件路径。
ApprovalUpgrader = Callable[["ApprovalRequest"], Awaitable[tuple[Outcome, bool]]]


async def default_upgrader(req: ApprovalRequest) -> tuple[Outcome, bool]:
    # 没有 TUI 通道时明确拒绝，避免子 Agent 悬挂。
    return Outcome.DENY_ONCE, True


__all__ = ["ApprovalUpgrader", "default_upgrader"]
