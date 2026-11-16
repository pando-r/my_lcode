"""Configuration loading and validation for mewcode."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

# 目前支持的两种LLM协议类型
VALID_PROTOCOLS = ("anthropic", "openai")

# 协议默认上下文窗口（token），未配置 context_window 时按此回退
DEFAULT_ANTHROPIC_CONTEXT_WINDOW = 200000
DEFAULT_OPENAI_CONTEXT_WINDOW = 128000


@dataclass
class ProviderConfig:
    name: str
    protocol: Literal["anthropic", "openai"]
    api_key: str
    model: str
    base_url: str | None = None
    thinking: bool = False
    context_window: int = 0


def effective_context_window(p: ProviderConfig) -> int:
    if p.context_window > 0:
        return p.context_window
    if p.protocol == "openai":
        return DEFAULT_OPENAI_CONTEXT_WINDOW
    return DEFAULT_ANTHROPIC_CONTEXT_WINDOW


@dataclass
class FeaturesConfig:
    coordinator_mode: bool = False
    fork_teammate: bool = False


@dataclass
class Config:
    providers: list[ProviderConfig] = field(default_factory=list)
    enable_subagent_background: bool | None = None
    features: FeaturesConfig = field(default_factory=FeaturesConfig)

    def effective_enable_subagent_background(self) -> bool:
        # N6：默认开启；False 时所有子 Agent 强制前台，Fork 路径直接报错。
        if self.enable_subagent_background is None:
            return True
        return self.enable_subagent_background


class ConfigError(Exception):
    # Raised when the configuration is missing or invalid.
    pass


# 公开接口
def load(path: str) -> Config:
    # Load and validate a YAML config file, returning a Config.
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在: {path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 解析失败: {exc}") from exc

    return _from_dict(raw)


# 核心校验函数
def _from_dict(raw: object) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是映射（含 providers 列表）")

    providers = raw.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ConfigError("providers 不能为空，且必须是列表")

    result: list[ProviderConfig] = []
    for idx, item in enumerate(providers):
        label = f"providers[{idx}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{label} 必须是映射")

        for field_name in ("name", "protocol", "api_key", "model"):
            value = item.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{label}.{field_name} 必须是非空字符串")

        protocol = item["protocol"]
        if protocol not in VALID_PROTOCOLS:
            raise ConfigError(
                f"{label}.protocol 必须是 {' / '.join(VALID_PROTOCOLS)} 之一，实际为: {protocol!r}"
            )

        result.append(
            ProviderConfig(
                name=item["name"].strip(),
                protocol=protocol,
                api_key=item["api_key"].strip(),
                model=item["model"].strip(),
                base_url=_optional_str(item.get("base_url")),
                thinking=bool(item.get("thinking", False)),
                context_window=_int_optional(item.get("context_window")),
            )
        )

    background = raw.get("enable_subagent_background")
    if background is None:
        background = raw.get("enableSubAgentBackground")
    features_raw = raw.get("features")
    features = FeaturesConfig()
    if isinstance(features_raw, dict):
        features = FeaturesConfig(
            coordinator_mode=bool(
                features_raw.get("coordinator_mode", features_raw.get("coordinatorMode", False))
            ),
            fork_teammate=bool(
                features_raw.get("fork_teammate", features_raw.get("forkTeammate", False))
            ),
        )
    return Config(
        providers=result,
        enable_subagent_background=_optional_bool(background),
        features=features,
    )


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_optional(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0
