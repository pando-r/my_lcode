from __future__ import annotations

import pytest

from lcode.command import Command, Kind, Registry
from lcode.command.ui import NopUI


def _cmd(name: str, aliases: list[str] | None = None, *, hidden: bool = False) -> Command:
    async def _handler(ui: NopUI) -> None:
        pass

    return Command(name, f"{name} desc", Kind.LOCAL, _handler, aliases or [], hidden)


def test_register_ok_and_lookup():
    reg = Registry()
    reg.register(_cmd("help", ["h"]))
    assert reg.lookup("help").name == "help"
    assert reg.lookup("HELP").name == "help"  # 大小写不敏感
    assert reg.lookup("h").name == "help"
    assert reg.lookup("nope") is None


def test_register_duplicate_name_raises():
    reg = Registry()
    reg.register(_cmd("help"))
    with pytest.raises(RuntimeError, match="help"):
        reg.register(_cmd("help"))


def test_register_duplicate_alias_raises():
    reg = Registry()
    reg.register(_cmd("help", ["h"]))
    with pytest.raises(RuntimeError, match="h"):
        reg.register(_cmd("hello", ["h"]))


def test_register_rejects_uppercase():
    reg = Registry()
    with pytest.raises(ValueError):
        reg.register(_cmd("Help"))


def test_visible_sorted_and_hidden_excluded():
    reg = Registry()
    reg.register(_cmd("status"))
    reg.register(_cmd("help"))
    reg.register(_cmd("secret", hidden=True))
    assert [c.name for c in reg.visible()] == ["help", "status"]
    assert reg.lookup("secret") is not None  # hidden 仍可被 dispatcher 命中


def test_prefix_match_only_names():
    reg = Registry()
    reg.register(_cmd("status"))
    reg.register(_cmd("session"))
    reg.register(_cmd("help", ["status-alias"]))

    assert [c.name for c in reg.prefix_match("/s")] == ["session", "status"]
    assert [c.name for c in reg.prefix_match("/")] == ["help", "session", "status"]
    assert reg.prefix_match("/status-alias") == []  # 不匹配别名
    assert reg.prefix_match("/desc") == []  # 不匹配描述
    assert reg.prefix_match("/S")[0].name == "session"  # 大小写不敏感
