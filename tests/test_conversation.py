from __future__ import annotations

from lcode.conversation import Conversation
from lcode.llm import Message, ToolCall, ToolResult


def test_conversation_order():
    conv = Conversation()
    conv.add_user("hi")
    conv.add_assistant("hello")
    conv.add_user("how are you?")
    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0].content == "hi"
    assert msgs[1].content == "hello"
    assert msgs[2].content == "how are you?"


def test_messages_returns_copy():
    conv = Conversation()
    conv.add_user("a")
    msgs = conv.messages()
    msgs.clear()
    assert len(conv.messages()) == 1


def test_tool_turns():
    conv = Conversation()
    conv.add_user("do it")
    conv.add_assistant_with_tool_calls("", [ToolCall(id="c1", name="read_file", input="{}")])
    conv.add_tool_results([ToolResult(tool_call_id="c1", content="ok")])
    conv.add_assistant("done")
    msgs = conv.messages()
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].tool_calls[0].name == "read_file"
    assert msgs[2].tool_results[0].content == "ok"


def test_last_role():
    conv = Conversation()
    assert conv.last_role() == ""
    conv.add_user("hi")
    assert conv.last_role() == "user"
    conv.add_tool_results([ToolResult(tool_call_id="x", content="y")])
    assert conv.last_role() == "tool"
    conv.add_assistant("bye")
    assert conv.last_role() == "assistant"


def test_callbacks_fire_on_append_and_replace():
    appended: list[Message] = []
    replaced: list[list[Message]] = []
    conv = Conversation(on_append=appended.append, on_replace=replaced.append)

    conv.add_user("a")
    conv.add_assistant("b")
    conv.add_assistant_with_tool_calls("", [ToolCall(id="c", name="x", input="{}")])
    conv.add_tool_results([ToolResult(tool_call_id="c", content="ok")])

    assert [m.role for m in appended] == ["user", "assistant", "assistant", "tool"]

    conv.replace_messages([Message(role="user", content="z")])
    assert len(replaced) == 1
    assert len(replaced[0]) == 1
    assert replaced[0][0].content == "z"
    assert conv.messages()[0].content == "z"


def test_callback_errors_do_not_break_conversation():
    def boom(_msg):
        raise RuntimeError("write failed")

    conv = Conversation(on_append=boom)
    conv.add_user("hi")
    assert conv.last_role() == "user"


def test_from_messages_does_not_fire_callbacks():
    appended: list[Message] = []
    conv = Conversation.from_messages(
        [Message(role="user", content="x")], on_append=appended.append
    )
    assert conv.messages()[0].content == "x"
    assert appended == []
