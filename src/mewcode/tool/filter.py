"""Multi-layer tool filtering for sub-agents."""

from __future__ import annotations

from dataclasses import dataclass, field

from mewcode.subagent.definition import Source

# 所有子 Agent 永远不能使用的工具名。
ALL_AGENT_DISALLOWED_TOOLS: list[str] = ["Agent"]

# 自定义（user/project/plugin）Agent 额外禁用的工具；本期为空，供后续扩展。
CUSTOM_AGENT_DISALLOWED_TOOLS: list[str] = []

# Team 协作工具：只在 teammate 上下文可见（复用旧名 TaskList/TaskGet/SendMessage）。
TEAMMATE_EXTRA_TOOLS: list[str] = [
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
]
TEAM_ONLY_TOOLS: list[str] = ["TaskCreate", "TaskUpdate"]
# 任何子 Agent / 队员都不能创建或删除 Team，只有主 Agent 可以。
TEAM_MANAGEMENT_TOOLS: list[str] = ["TeamCreate", "TeamDelete"]

# 后台 Agent 白名单：只保留基础读写/搜索/网络工具。
ASYNC_AGENT_ALLOWED_TOOLS: list[str] = [
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "bash",
    # 兼容 spec 的小写命名与仓库实际的 PascalCase 工具名。
    "load_skill",
    "install_skill",
    "LoadSkill",
    "InstallSkill",
]


def is_mcp_or_skill(name: str) -> bool:
    # MCP 工具按 mcp__ 前缀识别；Skill 相关工具已在白名单中显式列出。
    return name.startswith("mcp__")


@dataclass
class FilterParams:
    all: list[str]
    source: int = int(Source.BUILTIN)
    background: bool = False
    allowed: list[str] = field(default_factory=list)
    disallowed: list[str] = field(default_factory=list)
    keep_agent: bool = False  # Fork 路径保留 Agent 工具，靠 QuerySource 阻断嵌套
    teammate: bool = False  # Team 队员：注入协作工具


def apply_agent_tool_filter(p: FilterParams) -> list[str]:
    # F30 顺序：全局禁止 → 自定义禁止 → 后台白名单 → 定义黑名单 → 定义白名单。
    names = list(p.all)
    had_agent = "Agent" in names

    if not p.keep_agent:
        names = [name for name in names if name not in ALL_AGENT_DISALLOWED_TOOLS]

    # 只有主 Agent 能管理 Team；子 Agent / 队员一律剔除。
    names = [name for name in names if name not in TEAM_MANAGEMENT_TOOLS]

    if p.teammate:
        for tool_name in TEAMMATE_EXTRA_TOOLS:
            if tool_name in p.all and tool_name not in names:
                names.append(tool_name)
    else:
        names = [name for name in names if name not in TEAM_ONLY_TOOLS]

    if p.source >= int(Source.USER):
        names = [name for name in names if name not in CUSTOM_AGENT_DISALLOWED_TOOLS]

    if p.background:
        async_allowed = set(ASYNC_AGENT_ALLOWED_TOOLS)
        names = [name for name in names if name in async_allowed or is_mcp_or_skill(name)]
        # 后台白名单会剔除 Agent；Fork 明确要求保留，因此单独放回。
        if p.keep_agent and had_agent and "Agent" not in names:
            names.append("Agent")

    disallowed = set(p.disallowed)
    names = [name for name in names if name not in disallowed]

    if p.allowed:
        allowed_set = set(p.allowed)
        names = [name for name in names if name in allowed_set]

    return names


__all__ = [
    "ALL_AGENT_DISALLOWED_TOOLS",
    "ASYNC_AGENT_ALLOWED_TOOLS",
    "CUSTOM_AGENT_DISALLOWED_TOOLS",
    "FilterParams",
    "TEAMMATE_EXTRA_TOOLS",
    "TEAM_MANAGEMENT_TOOLS",
    "TEAM_ONLY_TOOLS",
    "apply_agent_tool_filter",
    "is_mcp_or_skill",
]
