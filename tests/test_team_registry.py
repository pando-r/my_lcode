from __future__ import annotations

from mewcode.team.registry import AgentNameRegistry


def test_register_resolve_and_reverse():
    registry = AgentNameRegistry()
    registry.register("alice", "agent-123")
    assert registry.resolve("alice") == "agent-123"
    assert registry.resolve("agent-123") == "agent-123"
    assert registry.name_of("agent-123") == "alice"
    assert registry.list_() == {"alice": "agent-123"}


def test_register_name_override():
    registry = AgentNameRegistry()
    registry.register("alice", "a1")
    registry.register("alice", "a2")
    assert registry.resolve("alice") == "a2"
    assert registry.name_of("a1") is None
    assert registry.name_of("a2") == "alice"


def test_register_same_id_different_name():
    registry = AgentNameRegistry()
    registry.register("alice", "a1")
    registry.register("bob", "a1")
    assert registry.resolve("alice") is None
    assert registry.resolve("bob") == "a1"
    assert registry.name_of("a1") == "bob"


def test_unregister():
    registry = AgentNameRegistry()
    registry.register("alice", "a1")
    registry.unregister("alice")
    assert registry.resolve("alice") is None
    registry.register("bob", "a2")
    registry.unregister_by_agent_id("a2")
    assert registry.name_of("a2") is None
