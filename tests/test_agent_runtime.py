from __future__ import annotations

from lcode.agent.runtime import SessionRuntime
from lcode.compact import new_session_context


def test_reset_for_new_session(tmp_path):
    old = new_session_context(str(tmp_path))
    runtime = SessionRuntime(
        turn_count=7,
        session=old,
        replacement=object(),
        recovery=object(),
        auto_tracking=object(),
        context_window=128000,
        usage_anchor=123,
        anchor_msg_len=9,
    )

    new = new_session_context(str(tmp_path))
    runtime.reset_for_new_session(new)

    assert runtime.session is new
    assert runtime.session.session_id == new.session_id
    assert runtime.turn_count == 0
    assert runtime.replacement is None
    assert runtime.recovery is None
    assert runtime.auto_tracking is None
    assert runtime.usage_anchor == 0
    assert runtime.anchor_msg_len == 0
    # context_window 跟随 provider，不随会话重置。
    assert runtime.context_window == 128000
