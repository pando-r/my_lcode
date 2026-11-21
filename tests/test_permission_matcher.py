from __future__ import annotations

import pytest

from lcode.permission.matcher import (
    ExactMatcher,
    GlobMatcher,
    NotMatcher,
    RegexMatcher,
    compile_matcher,
)
from lcode.permission.rule import Rule, RuleSet, parse_rule


def test_exact_matcher():
    m = compile_matcher("=git status", is_command=True)
    assert isinstance(m, ExactMatcher)
    assert m.match("git status")
    assert not m.match("git status --short")
    assert str(m) == "=git status"


def test_regex_matcher_uses_search():
    m = compile_matcher(r"~^git (push|pull)", is_command=True)
    assert isinstance(m, RegexMatcher)
    assert m.match("git push origin main")
    assert not m.match("npm install")
    assert str(m) == r"~^git (push|pull)"


def test_not_matcher_recurses():
    m = compile_matcher("!~^git", is_command=True)
    assert isinstance(m, NotMatcher)
    assert m.match("npm install")
    assert not m.match("git status")
    assert str(m) == "!~^git"

    m = compile_matcher("!=git status", is_command=True)
    assert m.match("git push")
    assert not m.match("git status")


def test_glob_command_and_path_semantics():
    cmd = compile_matcher("git *", is_command=True)
    assert isinstance(cmd, GlobMatcher)
    assert cmd.match("git status --short")
    assert not cmd.match("npm i")

    path = compile_matcher("src/**", is_command=False)
    assert path.match("src/a/b.py")
    assert not path.match("docs/x")

    shallow = compile_matcher("src/*.py", is_command=False)
    assert shallow.match("src/a.py")
    assert not shallow.match("src/a/b.py")


def test_glob_escapes_are_literal():
    literal_star = compile_matcher(r"\*", is_command=True)
    assert literal_star.match("*")
    assert not literal_star.match("anything")

    literal_prefix = compile_matcher(r"\=git status", is_command=True)
    assert literal_prefix.match("=git status")

    path = compile_matcher(r"src/\*.py", is_command=False)
    assert path.match("src/*.py")
    assert not path.match("src/a.py")


def test_compile_matcher_rejects_empty_and_bad_regex():
    with pytest.raises(ValueError, match="empty"):
        compile_matcher("", is_command=True)
    with pytest.raises(ValueError, match="invalid regex"):
        compile_matcher("!~[", is_command=True)


def test_parse_rule_matcher_forms():
    rule, err = parse_rule("Read(=src/a.py)")
    assert err is None and rule is not None
    assert isinstance(rule.matcher, ExactMatcher)
    assert rule.matcher.match("src/a.py")

    rule, err = parse_rule("Bash(~^git)")
    assert err is None and rule is not None
    assert rule.matcher.match("git status")

    rule, err = parse_rule("Write(src/**)")
    assert err is None and rule is not None
    assert rule.matcher.match("src/a/b.py")


def test_parse_rule_reports_errors_instead_of_raising():
    rule, err = parse_rule("")
    assert rule is None and err == "empty rule"
    rule, err = parse_rule("Bash(git")
    assert rule is None and err == "invalid rule syntax"
    rule, err = parse_rule("(git *)")
    assert rule is None and err == "invalid rule syntax"
    rule, err = parse_rule("Bash(~[)")
    assert rule is None and err is not None and err.startswith("invalid regex")


def test_rule_accepts_legacy_glob_string():
    rule = Rule(tool="Bash", matcher="git *", allow=True)
    assert isinstance(rule.matcher, GlobMatcher)
    assert rule.pattern == "git *"
    assert rule.matcher.match("git status")


def test_ruleset_supports_new_matchers():
    allow, _ = parse_rule("Bash(~^git)")
    deny, _ = parse_rule("Bash(=git push)")
    deny2, _ = parse_rule("Bash(~--force)")
    rs = RuleSet(allow=[allow], deny=[deny, deny2])
    assert rs.match("Bash", "git status")[0].name == "ALLOW"
    assert rs.match("Bash", "git push")[0].name == "DENY"
    assert rs.match("Bash", "git push --force")[0].name == "DENY"
