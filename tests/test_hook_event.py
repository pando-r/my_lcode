from __future__ import annotations

from mewcode.hook.event import BLOCKING_EVENTS, Event, is_blocking, parse_event
from mewcode.hook.matcher import eval_condition, get_by_path
from mewcode.hook.rule import AtomCondition, CombineMode, Condition
from mewcode.permission.matcher import ExactMatcher, GlobMatcher, NotMatcher


def test_all_events_parse():
    names = [
        "SessionStart",
        "SessionEnd",
        "SessionResume",
        "UserPromptSubmit",
        "Stop",
        "PreUserMessage",
        "PreToolUse",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "Notification",
    ]
    assert [parse_event(name) for name in names] == [Event(name) for name in names]
    assert parse_event("sessionstart") is None
    assert parse_event("Nope") is None


def test_only_two_blocking_events():
    assert is_blocking(Event.PRE_TOOL_USE)
    assert is_blocking(Event.USER_PROMPT_SUBMIT)
    assert BLOCKING_EVENTS == {Event.PRE_TOOL_USE, Event.USER_PROMPT_SUBMIT}
    for event in Event:
        if event not in BLOCKING_EVENTS:
            assert not is_blocking(event)


def test_get_by_path_walks_dicts():
    payload = {"tool_name": "Bash", "tool_input": {"command": "git status"}, "count": 2}
    assert get_by_path(payload, "tool_name") == "Bash"
    assert get_by_path(payload, "tool_input.command") == "git status"
    assert get_by_path(payload, "count") == "2"
    assert get_by_path(payload, "missing.path") == ""
    assert get_by_path(payload, "tool_name.deeper") == ""


def test_get_by_path_serializes_objects_stably():
    payload = {"b": 1, "a": [1, 2]}
    assert get_by_path(payload, "") == '{"a": [1, 2], "b": 1}'
    assert get_by_path({"flag": True}, "flag") == "True"
    assert get_by_path({"none": None}, "none") == ""


def test_eval_condition_none_is_true():
    assert eval_condition(None, {})


def test_eval_condition_all_of_requires_every_atom():
    condition = Condition(
        CombineMode.ALL_OF,
        [
            AtomCondition("tool_name", ExactMatcher("Bash")),
            AtomCondition("tool_input.command", GlobMatcher("git *", True)),
        ],
    )
    assert eval_condition(condition, {"tool_name": "Bash", "tool_input": {"command": "git status"}})
    assert not eval_condition(condition, {"tool_name": "Bash", "tool_input": {"command": "npm i"}})


def test_eval_condition_any_of_requires_one_atom():
    condition = Condition(
        CombineMode.ANY_OF,
        [
            AtomCondition("tool_name", ExactMatcher("Write")),
            AtomCondition("tool_input.path", NotMatcher(GlobMatcher("src/*", False))),
        ],
    )
    assert eval_condition(condition, {"tool_name": "Read"})
    assert eval_condition(condition, {"tool_name": "Write", "tool_input": {"path": "src/x"}})
    assert not eval_condition(condition, {"tool_name": "Read", "tool_input": {"path": "src/x"}})
