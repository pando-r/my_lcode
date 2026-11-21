from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from lcode.compact import new_session_context, open_session_context, parse_session_time
from lcode.llm import Message, ToolCall, ToolResult
from lcode.session import (
    Writer,
    clean_expired,
    list_sessions,
    load_session,
    truncate_orphaned_tool_calls,
)


def _write_session(root: Path, session_id: str, content: str, mtime: float | None = None) -> Path:
    directory = root / session_id
    directory.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"role": "user", "content": content, "ts": 1, "model": "test-model"},
        ensure_ascii=False,
    )
    jsonl = directory / "conversation.jsonl"
    jsonl.write_text(line + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(jsonl, (mtime, mtime))
    return directory


# ── writer ───────────────────────────────────────────────────────────────


def test_writer_append_and_read(tmp_path):
    writer = Writer(str(tmp_path))
    writer.model = "m1"
    writer.append(Message(role="user", content="hello"))
    writer.append(Message(role="assistant", content="hi"))
    writer.close()

    lines = (tmp_path / "conversation.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["role"] == "user"
    assert first["content"] == "hello"
    assert first["model"] == "m1"
    assert isinstance(first["ts"], int)

    second = json.loads(lines[1])
    assert second["role"] == "assistant"
    assert "model" not in second


def test_writer_tool_fields(tmp_path):
    writer = Writer(str(tmp_path))
    writer.append(
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="read_file", input="{}")],
        )
    )
    writer.append(Message(role="tool", tool_results=[ToolResult(tool_call_id="c1", content="ok")]))
    writer.close()

    lines = (tmp_path / "conversation.jsonl").read_text(encoding="utf-8").splitlines()
    call = json.loads(lines[0])
    result = json.loads(lines[1])
    assert call["tool_calls"][0]["name"] == "read_file"
    assert result["tool_results"][0]["content"] == "ok"


def test_compact_marker_and_load(tmp_path):
    writer = Writer(str(tmp_path))
    writer.append(Message(role="user", content="old"))
    writer.write_compact_marker()
    writer.append_all([Message(role="user", content="new")])
    writer.close()

    messages = load_session(str(tmp_path))
    assert [m.content for m in messages] == ["new"]


def test_open_existing_appends(tmp_path):
    writer = Writer(str(tmp_path))
    writer.append(Message(role="user", content="a"))
    writer.close()

    reopened = Writer.open_existing(str(tmp_path))
    reopened.append(Message(role="assistant", content="b"))
    reopened.close()

    lines = (tmp_path / "conversation.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# ── loading ──────────────────────────────────────────────────────────────


def test_load_skips_bad_lines(tmp_path):
    (tmp_path / "conversation.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"role": "user", "content": "a", "ts": 1}),
                "{bad json",
                json.dumps({"role": "assistant", "content": "b", "ts": 2}),
            ]
        ),
        encoding="utf-8",
    )
    messages = load_session(str(tmp_path))
    assert [m.content for m in messages] == ["a", "b"]


def test_load_truncates_orphaned_tool_calls(tmp_path):
    lines = [
        json.dumps({"role": "user", "content": "u", "ts": 1}),
        json.dumps(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "read_file", "input": "{}"}],
                "ts": 2,
            }
        ),
    ]
    (tmp_path / "conversation.jsonl").write_text("\n".join(lines), encoding="utf-8")

    messages = load_session(str(tmp_path))
    assert [m.role for m in messages] == ["user"]


def test_truncate_orphaned_keeps_complete_calls():
    messages = [
        Message(role="user", content="u"),
        Message(role="assistant", tool_calls=[ToolCall(id="c1", name="x", input="{}")]),
        Message(role="tool", tool_results=[ToolResult(tool_call_id="c1", content="ok")]),
        Message(role="assistant", content="done"),
    ]
    assert truncate_orphaned_tool_calls(messages) == messages


# ── listing ──────────────────────────────────────────────────────────────


def test_list_sessions_sorted_newest_first(tmp_path):
    _write_session(tmp_path, "20260101-000000-aaaa", "first", mtime=1000)
    _write_session(tmp_path, "20260102-000000-bbbb", "second", mtime=2000)
    _write_session(tmp_path, "20260103-000000-cccc", "third", mtime=3000)

    items = list_sessions(str(tmp_path))
    assert [item.title for item in items] == ["third", "second", "first"]
    assert items[0].model == "test-model"
    assert items[0].size > 0


def test_list_skips_old_format(tmp_path):
    _write_session(tmp_path, "1717000000-abc12345", "old")
    assert list_sessions(str(tmp_path)) == []


def test_list_truncates_title(tmp_path):
    _write_session(tmp_path, "20260101-000000-aaaa", "x" * 80)
    item = list_sessions(str(tmp_path))[0]
    assert len(item.title) == 50
    assert item.title.endswith("…")


# ── cleanup ──────────────────────────────────────────────────────────────


def test_clean_expired_only_new_format(tmp_path):
    old_id = (datetime.now() - timedelta(days=31)).strftime("%Y%m%d-%H%M%S") + "-abcd"
    new_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-beef"
    _write_session(tmp_path, old_id, "old")
    _write_session(tmp_path, new_id, "new")
    old_format = tmp_path / "1717000000-abc12345"
    old_format.mkdir()

    removed = clean_expired(str(tmp_path), timedelta(days=30))

    assert old_id in removed
    assert not (tmp_path / old_id).exists()
    assert (tmp_path / new_id).exists()
    assert old_format.exists()


# ── session id / context ─────────────────────────────────────────────────


def test_new_session_id_format(tmp_path):
    ctx = new_session_context(str(tmp_path))
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", ctx.session_id)
    assert parse_session_time(ctx.session_id)
    assert Path(ctx.session_dir).name == ctx.session_id
    assert ctx.spill_dir == str(Path(ctx.session_dir) / "tool-results")
    assert Path(ctx.spill_dir).is_dir()


def test_open_session_context(tmp_path):
    ctx = new_session_context(str(tmp_path))
    reopened = open_session_context(str(tmp_path), ctx.session_id)
    assert reopened.session_dir == ctx.session_dir
    assert reopened.spill_dir == ctx.spill_dir


def test_open_session_context_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_session_context(str(tmp_path), "20260101-000000-aaaa")


def test_parse_session_time_rejects_old_format():
    with pytest.raises(ValueError):
        parse_session_time("1717000000-abc12345")


def test_writer_path_property(tmp_path):
    writer = Writer(str(tmp_path))
    assert writer.path
    assert Path(writer.path).is_file()
    assert Path(writer.path).name == "conversation.jsonl"
    writer.close()
