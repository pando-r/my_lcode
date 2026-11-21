"""Built-in dangerous-command blacklist (heuristic, non-configurable, N1)."""

# 是权限系统的第一道防线——一个内置的、写死在代码里的危险命令黑名单。
from __future__ import annotations

import re

# 黑名单列表
# Heuristic defense, deliberately NOT exhaustive and NOT user-configurable.
_BLACKLIST: list[re.Pattern] = [
    re.compile(r"rm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|~|\$HOME|/\*|\s*$)"),
    re.compile(r"rm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)*\*\s*$"),
    re.compile(r"dd\s+.*of=/dev/(sd|hd|nvme|disk|zero)"),
    re.compile(r":\(\)\s*\{.*\|.*&.*\};?\s*:"),
    re.compile(r"mkfs\."),
    re.compile(r">\s*/dev/(sd|hd|nvme|disk)"),
    re.compile(r"chmod\s+-R\s+0?777\s+/"),
]


def hits_blacklist(command: str) -> bool:
    # 判断命令是否命中黑名单。
    # 返回True表示命中。
    return any(p.search(command) for p in _BLACKLIST)
