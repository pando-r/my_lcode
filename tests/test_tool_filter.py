from __future__ import annotations

from lcode.subagent import Source
from lcode.tool.filter import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
    FilterParams,
    apply_agent_tool_filter,
    is_mcp_or_skill,
)

BASE = [
    "read_file",
    "write_file",
    "edit_file",
    "bash",
    "glob",
    "grep",
    "LoadSkill",
    "InstallSkill",
    "TaskList",
    "Agent",
    "mcp__demo__search",
]


def test_constants():
    assert ALL_AGENT_DISALLOWED_TOOLS == ["Agent"]
    assert CUSTOM_AGENT_DISALLOWED_TOOLS == []
    assert "bash" in ASYNC_AGENT_ALLOWED_TOOLS
    assert "Agent" not in ASYNC_AGENT_ALLOWED_TOOLS


def test_default_removes_agent_only():
    allowed = apply_agent_tool_filter(FilterParams(all=BASE))
    assert "Agent" not in allowed
    assert set(allowed) == set(BASE) - {"Agent"}


def test_background_whitelist_keeps_basics_and_mcp():
    allowed = apply_agent_tool_filter(FilterParams(all=BASE, background=True))
    assert "bash" in allowed
    assert "LoadSkill" in allowed
    assert "InstallSkill" in allowed
    assert "mcp__demo__search" in allowed
    assert "TaskList" not in allowed
    assert "Agent" not in allowed


def test_fork_background_keeps_agent():
    allowed = apply_agent_tool_filter(FilterParams(all=BASE, background=True, keep_agent=True))
    assert "Agent" in allowed
    assert "TaskList" not in allowed


def test_disallowed_blacklist():
    allowed = apply_agent_tool_filter(FilterParams(all=BASE, disallowed=["bash"]))
    assert "bash" not in allowed
    assert "read_file" in allowed


def test_allowed_whitelist():
    allowed = apply_agent_tool_filter(FilterParams(all=BASE, allowed=["read_file", "grep"]))
    assert allowed == ["read_file", "grep"]


def test_whitelist_then_blacklist():
    allowed = apply_agent_tool_filter(
        FilterParams(
            all=BASE,
            allowed=["read_file", "bash"],
            disallowed=["bash"],
        )
    )
    assert allowed == ["read_file"]


def test_custom_agent_extra_disallowed(monkeypatch):
    import lcode.tool.filter as filter_mod

    monkeypatch.setattr(filter_mod, "CUSTOM_AGENT_DISALLOWED_TOOLS", ["bash"])
    builtin = apply_agent_tool_filter(FilterParams(all=BASE, source=Source.BUILTIN))
    custom = apply_agent_tool_filter(FilterParams(all=BASE, source=Source.PROJECT))
    assert "bash" in builtin
    assert "bash" not in custom


def test_teammate_collaboration_tools_visibility():
    extended = BASE + [
        "TaskCreate",
        "TaskUpdate",
        "TaskGet",
        "SendMessage",
        "TeamCreate",
        "TeamDelete",
    ]
    normal = apply_agent_tool_filter(FilterParams(all=extended))
    assert "TaskCreate" not in normal
    assert "TaskUpdate" not in normal
    assert "TeamCreate" not in normal
    assert "TeamDelete" not in normal

    teammate = apply_agent_tool_filter(FilterParams(all=extended, teammate=True))
    for tool_name in ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "SendMessage"):
        assert tool_name in teammate
    assert "TeamCreate" not in teammate
    assert "TeamDelete" not in teammate


def test_is_mcp_or_skill():
    assert is_mcp_or_skill("mcp__github__search")
    assert not is_mcp_or_skill("read_file")
