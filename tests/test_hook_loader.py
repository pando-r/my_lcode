from __future__ import annotations

from pathlib import Path

from mewcode.hook import Event
from mewcode.hook.loader import load
from mewcode.hook.rule import ActionType, CombineMode


def _project_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".mewcode" / "hooks.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def test_no_files_yields_empty_engine(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    engine = load(tmp_path)
    assert engine.rules == []
    assert engine.sources == []


def test_loads_full_rule_shape(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    _project_file(
        tmp_path,
        """
hooks:
  - name: guard-writes
    event: PreToolUse
    if:
      all_of:
        - field: tool_name
          match: {type: exact, value: Write}
        - field: tool_input.path
          match: {type: not, inner: {type: glob, value: "src/*"}}
    action:
      type: shell
      command: echo blocked >&2; exit 2
    only_once: true
    timeout: 5s
""",
    )
    engine = load(tmp_path)
    assert len(engine.rules) == 1
    rule = engine.rules[0]
    assert rule.name == "guard-writes"
    assert rule.event is Event.PRE_TOOL_USE
    assert rule.only_once is True
    assert rule.asyncio_mode is False
    assert rule.timeout_s == 5.0
    assert rule.action.type is ActionType.SHELL
    assert rule.condition is not None and rule.condition.mode is CombineMode.ALL_OF
    assert engine.sources == [str(tmp_path / ".mewcode" / "hooks.yaml")]


def test_project_and_user_files_merge_first_name_wins(tmp_path, monkeypatch, capsys):
    home = _isolate_home(tmp_path, monkeypatch)
    user = home / ".mewcode" / "hooks.yaml"
    user.parent.mkdir(parents=True)
    user.write_text(
        """
hooks:
  - name: shared
    event: Stop
    action: {type: prompt, text: user}
  - name: user-only
    event: SessionStart
    action: {type: prompt, text: hello}
""",
        encoding="utf-8",
    )
    _project_file(
        tmp_path,
        """
hooks:
  - name: shared
    event: Stop
    action: {type: prompt, text: project}
""",
    )

    engine = load(tmp_path)
    names = [rule.name for rule in engine.rules]
    assert names == ["shared", "user-only"]
    assert engine.rules[0].action.prompt.text == "project"
    assert engine.sources == [
        str(tmp_path / ".mewcode" / "hooks.yaml"),
        str(user),
    ]
    assert 'hook "shared": duplicate name, skipped' in capsys.readouterr().err


def test_bad_rules_are_skipped_but_file_keeps_valid_rules(tmp_path, monkeypatch, capsys):
    _isolate_home(tmp_path, monkeypatch)
    _project_file(
        tmp_path,
        """
hooks:
  - name: good
    event: Stop
    action: {type: prompt, text: ok}
  - name: unknown-event
    event: Nope
    action: {type: prompt, text: x}
  - name: async-blocking
    event: PreToolUse
    async: true
    action: {type: prompt, text: x}
  - name: bad-if
    event: Stop
    if: {all_of: [], any_of: []}
    action: {type: prompt, text: x}
  - name: bad-action
    event: Stop
    action: {type: shell}
  - name: bad-timeout
    event: Stop
    timeout: forever
    action: {type: prompt, text: x}
  - name: good2
    event: PostCompact
    action: {type: subagent, agent_name: reviewer, prompt: review}
""",
    )
    engine = load(tmp_path)
    assert [rule.name for rule in engine.rules] == ["good", "good2"]
    err = capsys.readouterr().err
    assert 'hook "unknown-event": unknown event "Nope", skipped' in err
    assert 'hook "async-blocking": async not allowed for blocking events, skipped' in err
    assert 'hook "bad-if": invalid if:' in err
    assert 'hook "bad-action": invalid action:' in err
    assert 'hook "bad-timeout": invalid timeout' in err


def test_async_allowed_on_non_blocking_event(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    _project_file(
        tmp_path,
        """
hooks:
  - name: telemetry
    event: PostToolUse
    async: true
    action: {type: prompt, text: x}
""",
    )
    engine = load(tmp_path)
    assert [rule.name for rule in engine.rules] == ["telemetry"]
    assert engine.rules[0].asyncio_mode is True


def test_unreadable_yaml_is_non_fatal(tmp_path, monkeypatch, capsys):
    _isolate_home(tmp_path, monkeypatch)
    _project_file(tmp_path, "hooks: [unclosed\n")
    engine = load(tmp_path)
    assert engine.rules == []
    assert "failed to parse" in capsys.readouterr().err


def test_top_level_hooks_must_be_list(tmp_path, monkeypatch, capsys):
    _isolate_home(tmp_path, monkeypatch)
    _project_file(tmp_path, "hooks: not-a-list\n")
    engine = load(tmp_path)
    assert engine.rules == []
    assert 'expected top-level "hooks" list' in capsys.readouterr().err


def test_conditions_and_all_actions_compile(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    _project_file(
        tmp_path,
        """
hooks:
  - name: any
    event: Notification
    if:
      any_of:
        - field: kind
          match: {type: regex, value: "^stream"}
    action: {type: http, url: "http://127.0.0.1:9/x", headers: {X-Test: "1"}, body: "k={kind}"}
  - name: sub
    event: SessionEnd
    action: {type: subagent, agent_name: cleaner, prompt: cleanup}
  - name: prompt
    event: Stop
    action: {type: prompt, text: done}
""",
    )
    engine = load(tmp_path)
    assert [rule.name for rule in engine.rules] == ["any", "sub", "prompt"]
    http = engine.rules[0].action.http
    assert http is not None
    assert http.method == "POST"
    assert http.headers == {"X-Test": "1"}
    assert http.body == "k={kind}"
    sub = engine.rules[1].action.subagent
    assert sub is not None and sub.agent_name == "cleaner"
