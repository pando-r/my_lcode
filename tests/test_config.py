from __future__ import annotations

import pytest

from mewcode.config import ConfigError, load


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_load_single_provider(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: Claude
    protocol: anthropic
    api_key: sk-test
    model: claude-sonnet-5-5
    thinking: true
""",
    )
    cfg = load(path)
    assert len(cfg.providers) == 1
    p = cfg.providers[0]
    assert p.name == "Claude"
    assert p.protocol == "anthropic"
    assert p.api_key == "sk-test"
    assert p.model == "claude-sonnet-5-5"
    assert p.base_url is None
    assert p.thinking is True


def test_load_multiple_providers(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m1}
  - {name: GPT, protocol: openai, api_key: b, model: m2, base_url: "https://example.com/v1"}
""",
    )
    cfg = load(path)
    assert len(cfg.providers) == 2
    assert cfg.providers[1].base_url == "https://example.com/v1"
    assert cfg.providers[1].thinking is False


def test_missing_api_key(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: Claude
    protocol: anthropic
    model: m1
""",
    )
    with pytest.raises(ConfigError, match="api_key"):
        load(path)


def test_invalid_protocol(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - name: X
    protocol: gemini
    api_key: k
    model: m
""",
    )
    with pytest.raises(ConfigError, match="protocol"):
        load(path)


def test_empty_providers(tmp_path):
    path = _write(tmp_path, "providers: []\n")
    with pytest.raises(ConfigError, match="providers"):
        load(path)


def test_enable_subagent_background_default_and_override(tmp_path):
    base = """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m}
"""
    cfg = load(_write(tmp_path, base))
    assert cfg.enable_subagent_background is None
    assert cfg.effective_enable_subagent_background() is True

    path = tmp_path / "off.yaml"
    path.write_text(base + "enable_subagent_background: false\n", encoding="utf-8")
    cfg = load(str(path))
    assert cfg.effective_enable_subagent_background() is False

    path = tmp_path / "camel.yaml"
    path.write_text(base + "enableSubAgentBackground: true\n", encoding="utf-8")
    assert load(str(path)).effective_enable_subagent_background() is True


def test_features_section(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m}
features:
  coordinator_mode: true
  fork_teammate: true
""",
    )
    cfg = load(path)
    assert cfg.features.coordinator_mode is True
    assert cfg.features.fork_teammate is True


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load(str(tmp_path / "nope.yaml"))


def test_invalid_yaml(tmp_path):
    path = _write(tmp_path, "providers: [unclosed\n")
    with pytest.raises(ConfigError):
        load(path)
