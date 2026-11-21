from __future__ import annotations

import pytest

from lcode.llm import ToolCall
from lcode.permission import Category, Decision, Mode, new_engine
from lcode.permission.blacklist import hits_blacklist
from lcode.permission.engine import mode_fallback
from lcode.permission.rule import RuleSet, match_pattern, parse_rule
from lcode.permission.sandbox import sandbox_ok


def test_blacklist():
    assert hits_blacklist("rm -rf /")
    assert hits_blacklist("rm -fr ~")
    assert hits_blacklist(":(){ :|:& };:")
    assert hits_blacklist("dd if=/dev/zero of=/dev/sda")
    assert not hits_blacklist("rm -rf ./build")
    assert not hits_blacklist("git status")


def test_sandbox(tmp_path):
    import os
    import sys
    import tempfile

    root = str(tmp_path)
    assert sandbox_ok(root, "a.txt")
    assert sandbox_ok(root, str(tmp_path / "a.txt"))
    assert not sandbox_ok(root, "/etc/passwd")
    assert not sandbox_ok(root, "../outside")
    # N9：系统临时目录为显式白名单。Windows 上没有 /tmp，中转目录是 %TEMP%。
    if sys.platform == "win32":
        tmp_file = os.path.join(tempfile.gettempdir(), "lcode-sandbox-test.txt")
    else:
        tmp_file = "/tmp/lcode-sandbox-test.txt"
    assert sandbox_ok(root, tmp_file)


def test_sandbox_ancestor(tmp_path):
    root = str(tmp_path)
    assert sandbox_ok(root, str(tmp_path / "a" / "b" / "c.txt"))


def test_temp_roots_non_empty_and_deduped():
    from lcode.permission.sandbox import temp_roots

    roots = temp_roots()
    assert roots, "N9 白名单不能为空，否则临时目录中转会全部被拒"
    import os

    keys = [os.path.normcase(os.path.normpath(r)) for r in roots]
    assert len(keys) == len(set(keys))


def test_temp_whitelist_only_direct_children(tmp_path):
    # 中转文件是单层的；子目录必须被拒，否则项目外的兄弟目录能顺着白名单逃出去。
    import os

    from lcode.permission.sandbox import sandbox_ok, temp_roots

    temp_root = temp_roots()[0]
    root = str(tmp_path)

    assert sandbox_ok(root, os.path.join(temp_root, "lcode-direct.txt"))
    assert not sandbox_ok(root, os.path.join(temp_root, "some-dir", "deep.txt"))


def test_sandbox_case_insensitive_on_windows(tmp_path):
    # Windows 路径大小写不敏感：同一目录的大小写变体不能判成越界。
    import sys

    root = str(tmp_path)
    if sys.platform != "win32":
        pytest.skip("只在 Windows 上有意义")
    assert sandbox_ok(root, root.upper())
    assert sandbox_ok(root, root.lower())


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
    (tmp_path / ".lcode").mkdir()
    (tmp_path / ".lcode" / "settings.yaml").write_text(
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
    (tmp_path / ".lcode").mkdir()
    (tmp_path / ".lcode" / "settings.yaml").write_text(
        'permissions:\n  allow:\n    - "Bash(!~[)"\n', encoding="utf-8"
    )
    engine, err = new_engine(str(tmp_path))
    assert engine is not None and err is None
    assert engine.local.allow == []
    captured = capsys.readouterr()
    assert 'rule "Bash(!~[)" parse failed:' in captured.err


def test_settings_degradation(tmp_path):
    (tmp_path / ".lcode").mkdir()
    (tmp_path / ".lcode" / "settings.yaml").write_text("invalid: [unclosed\n", encoding="utf-8")
    engine, err = new_engine(str(tmp_path))
    assert engine is not None  # never fatal for malformed settings
    assert err is None
