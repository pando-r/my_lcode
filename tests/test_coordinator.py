from __future__ import annotations

import pytest

from lcode import coordinator
from lcode.config import Config, FeaturesConfig, ProviderConfig


def _cfg(feature: bool) -> Config:
    return Config(
        providers=[ProviderConfig("p", "openai", "k", "m")],
        features=FeaturesConfig(coordinator_mode=feature),
    )


@pytest.mark.parametrize(
    ("feature", "env", "expected"),
    [
        (False, "", False),
        (False, "1", False),
        (True, "", False),
        (True, "1", True),
        (True, "TRUE", True),
        (True, "yes", True),
        (True, "0", False),
    ],
)
def test_double_lock(monkeypatch, feature, env, expected):
    if env:
        monkeypatch.setenv("LCODE_COORDINATOR_MODE", env)
    else:
        monkeypatch.delenv("LCODE_COORDINATOR_MODE", raising=False)
    assert coordinator.is_enabled(_cfg(feature)) is expected


def test_allowed_tools_excludes_write_tools():
    tools = coordinator.allowed_tools()
    assert "bash" in tools
    assert "Agent" in tools
    assert "write_file" not in tools
    assert "edit_file" not in tools


def test_prompt_suffix_has_discipline():
    suffix = coordinator.system_prompt_suffix()
    assert "Coordinator" in suffix
    assert "等待汇报" in suffix
    assert "write_file" in suffix
