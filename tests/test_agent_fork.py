from __future__ import annotations

from lcode.agent.fork import (
    FORK_BOILERPLATE,
    FORK_BOILERPLATE_TAG,
    build_forked_messages,
    is_fork_context,
)
from lcode.llm import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ToolCall,
    ToolResult,
)


def test_empty_parent_gets_boilerplate_and_task():
    messages = build_forked_messages([], "do work")
    assert len(messages) == 1
    assert messages[0].role == ROLE_USER
    assert messages[0].content == FORK_BOILERPLATE + "do work"
    assert FORK_BOILERPLATE_TAG in messages[0].content


def test_complete_pair_is_copied_untouched():
    parent = [
        Message(role=ROLE_USER, content="hello"),
        Message(
            role=ROLE_ASSISTANT,
            content="",
            tool_calls=[ToolCall(id="c1", name="read_file", input="{}")],
        ),
        Message(
            role=ROLE_TOOL,
            tool_results=[ToolResult(tool_call_id="c1", content="data")],
        ),
        Message(role=ROLE_ASSISTANT, content="answer"),
    ]
    forked = build_forked_messages(parent, "task")
    assert [m.role for m in forked[:4]] == [m.role for m in parent]
    assert forked[3].content == "answer"
    assert forked[4].role == ROLE_USER
    assert forked[4].content == FORK_BOILERPLATE + "task"
    # 深拷贝：修改结果不影响父对话
    forked[2].tool_results[0].content = "changed"
    assert parent[2].tool_results[0].content == "data"


def test_dangling_tool_calls_get_placeholders():
    parent = [
        Message(role=ROLE_USER, content="hello"),
        Message(
            role=ROLE_ASSISTANT,
            content="",
            tool_calls=[
                ToolCall(id="c1", name="read_file", input="{}"),
                ToolCall(id="c2", name="grep", input="{}"),
            ],
        ),
    ]
    forked = build_forked_messages(parent, "task")
    assert forked[2].role == ROLE_TOOL
    assert len(forked[2].tool_results) == 2
    assert all(r.content == "[forked, skipped]" for r in forked[2].tool_results)
    assert all(r.is_error for r in forked[2].tool_results)
    assert forked[3].role == ROLE_USER


def test_partial_pairs_only_missing_are_filled():
    parent = [
        Message(
            role=ROLE_ASSISTANT,
            content="",
            tool_calls=[
                ToolCall(id="c1", name="read_file", input="{}"),
                ToolCall(id="c2", name="grep", input="{}"),
            ],
        ),
        Message(
            role=ROLE_TOOL,
            tool_results=[ToolResult(tool_call_id="c1", content="data")],
        ),
    ]
    forked = build_forked_messages(parent, "task")
    assert forked[2].role == ROLE_TOOL
    assert [r.tool_call_id for r in forked[2].tool_results] == ["c2"]


def test_is_fork_context():
    assert not is_fork_context([])
    assert is_fork_context([Message(role=ROLE_USER, content=FORK_BOILERPLATE + "x")])
    assert is_fork_context(
        [
            Message(
                role=ROLE_TOOL,
                tool_results=[ToolResult(tool_call_id="c", content=FORK_BOILERPLATE_TAG)],
            )
        ]
    )
    assert not is_fork_context([Message(role=ROLE_USER, content="normal")])
