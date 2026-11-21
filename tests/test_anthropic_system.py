from __future__ import annotations

from lcode.config import ProviderConfig
from lcode.llm import Request, System
from lcode.llm.anthropic_provider import AnthropicProvider


def _provider() -> AnthropicProvider:
    return AnthropicProvider(ProviderConfig(name="x", protocol="anthropic", api_key="k", model="m"))


def test_system_blocks_cache_control():
    p = _provider()
    req = Request(system=System(stable="STABLE", environment="ENV"))
    blocks = p._build_system_blocks(req)
    assert len(blocks) == 2
    assert blocks[0]["text"] == "STABLE"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["text"] == "ENV"
    assert "cache_control" not in blocks[1]


def test_system_blocks_no_environment():
    p = _provider()
    req = Request(system=System(stable="STABLE"))
    blocks = p._build_system_blocks(req)
    assert len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


def test_append_reminder_to_user():
    p = _provider()
    messages = [{"role": "user", "content": "hi"}]
    p._append_reminder(messages, "<system-reminder>X</system-reminder>")
    assert messages[0]["role"] == "user"
    assert isinstance(messages[0]["content"], list)
    assert messages[0]["content"][-1]["text"] == "<system-reminder>X</system-reminder>"
