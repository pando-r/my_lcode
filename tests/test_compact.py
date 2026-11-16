from __future__ import annotations

import math

from mewcode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    estimate_tokens,
    new_session_context,
    usage_anchor,
)
from mewcode.compact.layer1 import build_preview, offload_and_snip
from mewcode.compact.recovery import build_recovery_attachment, render_file_block
from mewcode.compact.state import FileReadRecord
from mewcode.compact.summary_prompt import build_summary_prompt, extract_summary
from mewcode.config import ProviderConfig, effective_context_window
from mewcode.conversation import Conversation
from mewcode.llm import Message, ToolDefinition, ToolResult, Usage

# ── token ────────────────────────────────────────────────────────────────


def test_usage_anchor_sum():
    u = Usage(input_tokens=10, output_tokens=20, cache_read=3, cache_write=7)
    assert usage_anchor(u) == 40


def test_estimate_tokens():
    m = Message(role="user", content="hello")  # 5 bytes
    assert estimate_tokens(0, [m], 0) == math.ceil(5 / 3.5)
    assert estimate_tokens(1000, [m], 1) == 1000  # anchor covers the only message
    assert estimate_tokens(1000, [Message(role="user", content="a"), m], 1) == 1000 + math.ceil(
        5 / 3.5
    )


# ── state ────────────────────────────────────────────────────────────────


def test_decide_once_freeze():
    st = ContentReplacementState()
    out = st.decide_once("id1", "orig", lambda: ("replaced", "PREVIEW"))
    assert out == "PREVIEW"
    # frozen: decide callback is no longer called
    out2 = st.decide_once("id1", "orig", lambda: ("replaced", "OTHER"))
    assert out2 == "PREVIEW"


def test_decide_once_skip_not_marked():
    st = ContentReplacementState()
    assert st.decide_once("id1", "orig", lambda: ("skip", "")) == "orig"
    # retried next round: callback runs again
    assert st.decide_once("id1", "orig", lambda: ("replaced", "P")) == "P"


def test_circuit_breaker():
    cb = CompactCircuitBreaker()
    cb.record_failure()
    cb.record_failure()
    assert not cb.tripped()
    cb.record_failure()
    assert cb.tripped()
    cb.record_success()
    assert not cb.tripped()


def test_recovery_snapshot_order():
    rs = RecoveryState()
    rs.record_file("a.txt", "A")
    rs.record_file("b.txt", "B")
    rs.record_file("c.txt", "C")
    assert [r.content for r in rs.snapshot()] == ["C", "B", "A"]


def test_new_session_context(tmp_path):
    ctx = new_session_context(str(tmp_path))
    assert "-" in ctx.session_id
    import os

    assert os.path.isdir(ctx.spill_dir)


# ── layer1 ───────────────────────────────────────────────────────────────


def test_build_preview_stable():
    a = build_preview(100, "head", "/p/x")
    b = build_preview(100, "head", "/p/x")
    assert a == b
    assert "original size: 100 bytes" in a


def test_offload_single(tmp_path):
    ctx = new_session_context(str(tmp_path))
    st = ContentReplacementState()
    big = "x" * 60000
    msgs = [Message(role="tool", tool_results=[ToolResult(tool_call_id="t1", content=big)])]
    out = offload_and_snip(msgs, st, ctx)
    assert out[0].tool_results[0].content != big
    assert "t1" in out[0].tool_results[0].content  # preview mentions path


def test_offload_decision_freeze(tmp_path):
    ctx = new_session_context(str(tmp_path))
    st = ContentReplacementState()
    msgs = [Message(role="tool", tool_results=[ToolResult(tool_call_id="t1", content="x" * 60000)])]
    out1 = offload_and_snip(msgs, st, ctx)
    out2 = offload_and_snip(msgs, st, ctx)
    assert out1[0].tool_results[0].content == out2[0].tool_results[0].content


# ── summary prompt ───────────────────────────────────────────────────────


def test_extract_summary():
    assert extract_summary("x<summary>body</summary>y") == "body"
    assert extract_summary("no tags") == "no tags"


def test_build_summary_prompt():
    msgs = [Message(role="user", content="hi")]
    out = build_summary_prompt(msgs)
    assert len(out) == 1
    assert "主要请求和意图" in out[0].content
    assert "hi" in out[0].content


# ── recovery ─────────────────────────────────────────────────────────────


def test_render_file_block_truncate():
    rec = FileReadRecord(
        path="/f", content="y" * 30000, timestamp=__import__("datetime").datetime.now()
    )
    out = render_file_block(rec)
    assert "(content truncated)" in out


def test_build_recovery_attachment(tmp_path):
    from datetime import datetime

    snapshot = [FileReadRecord(path="/a", content="A", timestamp=datetime.now())]
    defs = [ToolDefinition(name="read_file", description="read", input_schema={"type": "object"})]
    out = build_recovery_attachment(snapshot, defs)
    assert "最近读过的文件" in out
    assert "read_file" in out
    assert "边界提示" in out


# ── config ───────────────────────────────────────────────────────────────


def test_effective_context_window():
    assert effective_context_window(ProviderConfig("x", "anthropic", "k", "m")) == 200000
    assert effective_context_window(ProviderConfig("x", "openai", "k", "m")) == 128000
    assert (
        effective_context_window(ProviderConfig("x", "anthropic", "k", "m", context_window=80000))
        == 80000
    )


# ── conversation ─────────────────────────────────────────────────────────


def test_replace_history_deep_copy():
    conv = Conversation()
    conv.add_user("a")
    msgs = [Message(role="user", content="b")]
    conv.replace_history(msgs)
    msgs[0].content = "changed"
    assert conv.messages()[0].content == "b"
