from __future__ import annotations

from pathlib import Path

import pytest

from lcode.permission import Mode
from lcode.subagent.definition import Definition, Source
from lcode.subagent.parser import AgentParseError, parse_definition, parse_file


def _parse(text: str, source: Source = Source.PROJECT) -> Definition:
    return parse_definition(text.encode("utf-8"), "/tmp/agent.md", source)


def test_definition_fields_defaults():
    definition = _parse("---\nname: demo\ndescription: demo role\n---\n\nbody")
    assert isinstance(definition, Definition)
    assert definition.name == "demo"
    assert definition.description == "demo role"
    assert definition.tools == []
    assert definition.disallowed_tools == []
    assert definition.model == "inherit"
    assert definition.max_turns == 0
    assert definition.permission_mode is Mode.DEFAULT
    assert definition.dont_ask is False
    assert definition.background is False
    assert definition.system_prompt == "body"
    assert definition.file_path == "/tmp/agent.md"
    assert definition.source is Source.PROJECT
    assert definition.is_fork() is False


def test_parse_all_fields():
    definition = _parse(
        """---
name: Explore
description: explore role
tools: [read_file, grep]
disallowedTools:
  - write_file
model: haiku
maxTurns: 12
permissionMode: plan
background: true
---

system body
line 2
"""
    )
    assert definition.tools == ["read_file", "grep"]
    assert definition.disallowed_tools == ["write_file"]
    assert definition.model == "haiku"
    assert definition.max_turns == 12
    assert definition.permission_mode is Mode.PLAN
    assert definition.background is True
    assert "system body" in definition.system_prompt
    assert "line 2" in definition.system_prompt


def test_snake_case_frontmatter_aliases():
    definition = _parse(
        """---
name: snake
description: d
disallowed_tools: [write_file]
max_turns: 4
permission_mode: dontAsk
---
body"""
    )
    assert definition.disallowed_tools == ["write_file"]
    assert definition.max_turns == 4
    assert definition.dont_ask is True


def test_plan_mode_required_aliases():
    camel = _parse("---\nname: p\ndescription: d\nplanModeRequired: true\n---\nbody")
    snake = _parse("---\nname: p\ndescription: d\nplan_mode_required: true\n---\nbody")
    assert camel.plan_mode_required is True
    assert snake.plan_mode_required is True


def test_dont_ask_mode():
    definition = _parse("---\nname: auto\ndescription: d\npermissionMode: dontAsk\n---\nbody")
    assert definition.dont_ask is True
    assert definition.permission_mode is Mode.DEFAULT


def test_invalid_model_and_mode_fall_back_with_warnings(capsys):
    definition = _parse(
        "---\nname: bad\ndescription: d\nmodel: gpt-4\npermissionMode: weird\n---\nbody"
    )
    assert definition.model == "inherit"
    assert definition.permission_mode is Mode.DEFAULT
    assert definition.dont_ask is False
    err = capsys.readouterr().err
    assert 'unknown model "gpt-4"' in err
    assert 'unknown permissionMode "weird"' in err


def test_isolation_worktree():
    definition = _parse("---\nname: iso\ndescription: d\nisolation: worktree\n---\nbody")
    assert definition.isolation == "worktree"


def test_isolation_defaults_empty():
    definition = _parse("---\nname: iso\ndescription: d\n---\nbody")
    assert definition.isolation == ""


def test_invalid_isolation_warns(capsys):
    definition = _parse("---\nname: iso\ndescription: d\nisolation: container\n---\nbody")
    assert definition.isolation == ""
    assert 'unknown isolation "container"' in capsys.readouterr().err


def test_missing_name_or_description_raises():
    with pytest.raises(AgentParseError, match="name"):
        _parse("---\ndescription: d\n---\nbody")
    with pytest.raises(AgentParseError, match="description"):
        _parse("---\nname: demo\n---\nbody")


def test_invalid_name_raises():
    with pytest.raises(AgentParseError, match="非法 name"):
        _parse("---\nname: 1bad\ndescription: d\n---\nbody")


def test_unclosed_frontmatter_raises():
    with pytest.raises(AgentParseError, match="未闭合"):
        _parse("---\nname: demo\ndescription: d\nbody")


def test_bom_is_stripped():
    definition = parse_definition(
        "﻿---\nname: bom\ndescription: d\n---\nbody".encode(),
        "x",
        Source.USER,
    )
    assert definition.name == "bom"


def test_invalid_tools_field_warns(capsys):
    definition = _parse("---\nname: demo\ndescription: d\ntools: read_file\n---\nbody")
    assert definition.tools == []
    assert "tools must be a list" in capsys.readouterr().err


def test_invalid_max_turns_warns(capsys):
    definition = _parse("---\nname: demo\ndescription: d\nmaxTurns: many\n---\nbody")
    assert definition.max_turns == 0
    assert "maxTurns must be an integer" in capsys.readouterr().err


def test_parse_file(tmp_path):
    path = tmp_path / "demo.md"
    path.write_text("---\nname: demo\ndescription: d\n---\nbody", encoding="utf-8")
    definition = parse_file(path, Source.USER)
    assert definition.name == "demo"
    assert Path(definition.file_path) == path.resolve()
