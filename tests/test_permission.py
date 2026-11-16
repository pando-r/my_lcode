from __future__ import annotations

import pytest

from mewcode.llm import ToolCall
from mewcode.permission import Category, Decision, Mode, new_engine
from mewcode.permission.blacklist import hits_blacklist
from mewcode.permission.engine import mode_fallback
from mewcode.permission.rule import RuleSet, match_pattern, parse_rule
from mewcode.permission.sandbox import sandbox_ok


def test_blacklist():
    assert hits_blacklist("rm -rf /")
    assert hits_blacklist("rm -fr ~")
    assert hits_blacklist(":(){ :|:& };:")
    assert hits_blacklist("dd if=/dev/zero of=/dev/sda")
    assert not hits_blacklist("rm -rf ./build")
    assert not hits_blacklist("git status")


def test_sandbox(tmp_path):
    root = str(tmp_path)
    assert sandbox_ok(root, "a.txt")
    assert sandbox_ok(root, str(tmp_path / "a.txt"))
    assert not sandbox_ok(root, "/etc/passwd")
    assert not sandbox_ok(root, "../outside")
    # N9：系统临时目录为显式白名单。
    assert sandbox_ok(root, "/tmp/mewcode-sandbox-test.txt")


def test_sandbox_ancestor(tmp_path):
    root = str(tmp_path)
    assert sandbox_ok(root, str(tmp_path / "a" / "b" / "c.txt"))


def test_sandbox_symlink(tmp_path):
    root = str(tmp_path)
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported on this platform")
    assert not sandbox_ok(root, str(link))


def test_rules_parse_and_match():
    r, err = parse_rule("Bash(git *)")
    assert err is None and r is not None
    assert r.tool == "Bash" and str(r.matcher) == "git *"
    r, err = parse_rule("Read")
    assert err is None and r is not None and r.matcher is None
    assert match_pattern("git *", "git status", False)
    assert not match_pattern("git *", "npm i", False)
    assert match_pattern("src/**", "src/a/b.py", True)
    assert not match_pattern("src/**", "docs/x", True)


def test_ruleset_deny_priority():
    rs = RuleSet()
    rs.allow.append(parse_rule("Bash(git *)")[0])
    rs.deny.append(parse_rule("Bash(git push)")[0])
    decision, hit = rs.match("Bash", "git push")
    assert decision is Decision.DENY and hit


def test_mode_matrix():
    assert mode_fallback(Mode.DEFAULT, Category.READ) is Decision.ALLOW
    assert mode_fallback(Mode.DEFAULT, Category.WRITE) is Decision.ASK
    assert mode_fallback(Mode.DEFAULT, Category.EXEC) is Decision.ASK
    assert mode_fallback(Mode.ACCEPT_EDITS, Category.WRITE) is Decision.ALLOW
    assert mode_fallback(Mode.ACCEPT_EDITS, Category.EXEC) is Decision.ASK
    assert mode_fallback(Mode.BYPASS, Category.EXEC) is Decision.ALLOW
    assert mode_fallback(Mode.PLAN, Category.WRITE) is Decision.ASK


def test_engine_check_blacklist_not_bypassed(tmp_path):
    engine, _ = new_engine(str(tmp_path))
    call = ToolCall(id="c", name="bash", input='{"command": "rm -rf /"}')
    decision, _ = engine.check(Mode.BYPASS, call, False)
    assert decision is Decision.DENY


def test_engine_check_sandbox(tmp_path):
    engine, _ = new_engine(str(tmp_path))
    call = ToolCall(id="c", name="write_file", input='{"path": "/etc/passwd", "content": "x"}')
    decision, _ = engine.check(Mode.DEFAULT, call, False)
    assert decision is Decision.DENY


def test_engine_check_mode_ask(tmp_path):
    engine, _ = new_engine(str(tmp_path))
    call = ToolCall(id="c", name="write_file", input='{"path": "a.txt", "content": "x"}')
    decision, _ = engine.check(Mode.DEFAULT, call, False)
    assert decision is Decision.ASK


def test_engine_check_allow_rule(tmp_path):
    (tmp_path / ".mewcode").mkdir()
    (tmp_path / ".mewcode" / "settings.yaml").write_text(
        'permissions:\n  allow:\n    - "Write(*)"\n', encoding="utf-8"
    )
    engine, _ = new_engine(str(tmp_path))
    call = ToolCall(id="c", name="write_file", input='{"path": "a.txt", "content": "x"}')
    decision, _ = engine.check(Mode.DEFAULT, call, False)
    assert decision is Decision.ALLOW


def test_engine_check_invalid_file_args(tmp_path):
    engine, _ = new_engine(str(tmp_path))
    call = ToolCall(id="c", name="read_file", input='{"no_path": 1}')
    decision, _ = engine.check(Mode.BYPASS, call, True)
    assert decision is Decision.DENY


def test_settings_rule_parse_failure_is_reported_and_skipped(tmp_path, capsys):
    (tmp_path / ".mewcode").mkdir()
    (tmp_path / ".mewcode" / "settings.yaml").write_text(
        'permissions:\n  allow:\n    - "Bash(!~[)"\n', encoding="utf-8"
    )
    engine, err = new_engine(str(tmp_path))
    assert engine is not None and err is None
    assert engine.local.allow == []
    captured = capsys.readouterr()
    assert 'rule "Bash(!~[)" parse failed:' in captured.err


def test_settings_degradation(tmp_path):
    (tmp_path / ".mewcode").mkdir()
    (tmp_path / ".mewcode" / "settings.yaml").write_text("invalid: [unclosed\n", encoding="utf-8")
    engine, err = new_engine(str(tmp_path))
    assert engine is not None  # never fatal for malformed settings
    assert err is None
