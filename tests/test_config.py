from __future__ import annotations

import pytest

from lcode.config import ConfigError, load


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


def test_vision_section(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m}
vision:
  protocol: openai
  api_key: sk-vision
  model: qwen3-vl-plus
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  max_image_side: 1024
  timeout: 60
""",
    )
    cfg = load(path)
    assert cfg.vision is not None
    assert cfg.vision.ready() is True
    assert cfg.vision.protocol == "openai"
    assert cfg.vision.model == "qwen3-vl-plus"
    assert cfg.vision.max_image_side == 1024
    assert cfg.vision.timeout == 60.0


def test_vision_absent_is_none(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m}
""",
    )
    assert load(path).vision is None


def test_vision_missing_model(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m}
vision:
  protocol: openai
  api_key: sk-vision
""",
    )
    with pytest.raises(ConfigError, match="vision.model"):
        load(path)


def test_vision_defaults_timeout_and_side(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m}
vision: {api_key: k, model: v}
""",
    )
    vision = load(path).vision
    assert vision is not None
    assert vision.protocol == "openai"
    assert vision.max_image_side == 1568
    assert vision.timeout == 120.0


def test_audio_section_cloud(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m}
audio:
  backend: openai
  api_key: sk-audio
  model: whisper-1
  language: en
  timeout: 300
""",
    )
    cfg = load(path)
    assert cfg.audio is not None
    assert cfg.audio.ready() is True
    assert cfg.audio.backend == "openai"
    assert cfg.audio.language == "en"
    assert cfg.audio.timeout == 300.0


def test_audio_section_local_needs_no_key(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m}
audio:
  backend: local
  local_model: base
""",
    )
    audio = load(path).audio
    assert audio is not None
    assert audio.ready() is True
    assert audio.local_model == "base"
    assert audio.language == "zh"


def test_audio_cloud_without_key_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m}
audio:
  backend: openai
  model: whisper-1
""",
    )
    with pytest.raises(ConfigError, match="audio.api_key"):
        load(path)


def test_audio_invalid_backend(tmp_path):
    path = _write(
        tmp_path,
        """
providers:
  - {name: Claude, protocol: anthropic, api_key: a, model: m}
audio: {backend: whisper.cpp}
""",
    )
    with pytest.raises(ConfigError, match="audio.backend"):
        load(path)


def test_invalid_yaml(tmp_path):
    path = _write(tmp_path, "providers: [unclosed\n")
    with pytest.raises(ConfigError):
        load(path)


# ── ${VAR} 展开：密钥不必写进配置文件 ────────────────────────────────────


def test_api_key_from_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TEST_KEY", "sk-from-env")
    path = _write(
        tmp_path,
        """
providers:
  - {name: P, protocol: openai, api_key: "${MY_TEST_KEY}", model: m}
""",
    )
    assert load(path).providers[0].api_key == "sk-from-env"


def test_vision_api_key_from_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_VISION_KEY", "sk-vision-env")
    path = _write(
        tmp_path,
        """
providers:
  - {name: P, protocol: openai, api_key: a, model: m}
vision:
  api_key: "${MY_VISION_KEY}"
  model: qwen3-vl-plus
""",
    )
    vision = load(path).vision
    assert vision is not None
    assert vision.api_key == "sk-vision-env"
    assert vision.ready() is True


def test_undefined_env_var_is_rejected_not_silently_empty(tmp_path, monkeypatch):
    # 写错变量名时，以前会得到一个「看起来有值」的空串 → 运行期才 401。
    # 现在在配置期就报错，并提示可能是变量没设。
    monkeypatch.delenv("TYPO_KEY", raising=False)
    path = _write(
        tmp_path,
        """
providers:
  - {name: P, protocol: openai, api_key: a, model: m}
vision:
  api_key: "${TYPO_KEY}"
  model: qwen3-vl-plus
""",
    )
    with pytest.raises(ConfigError, match="环境变量已设置"):
        load(path)


def test_base_url_env_var_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_BASE", "https://example.com/v1")
    path = _write(
        tmp_path,
        """
providers:
  - {name: P, protocol: openai, api_key: a, model: m, base_url: "${MY_BASE}"}
""",
    )
    assert load(path).providers[0].base_url == "https://example.com/v1"


def test_undefined_base_url_var_becomes_none(tmp_path, monkeypatch):
    # 可选字段展开成空串时按「未设置」处理，而不是留一个空字符串。
    monkeypatch.delenv("NO_SUCH_BASE", raising=False)
    path = _write(
        tmp_path,
        """
providers:
  - {name: P, protocol: openai, api_key: a, model: m, base_url: "${NO_SUCH_BASE}"}
""",
    )
    assert load(path).providers[0].base_url is None
