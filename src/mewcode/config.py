"""Configuration loading and validation for mewcode."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

# 显式指定配置文件路径的环境变量（优先级最高）
CONFIG_ENV = "MEWCODE_CONFIG"

# 目前支持的两种LLM协议类型
VALID_PROTOCOLS = ("anthropic", "openai")

# 协议默认上下文窗口（token），未配置 context_window 时按此回退
DEFAULT_ANTHROPIC_CONTEXT_WINDOW = 200000
DEFAULT_OPENAI_CONTEXT_WINDOW = 128000

#LLM服务商的配置项
@dataclass
class ProviderConfig:
    name: str
    protocol: Literal["anthropic", "openai"]
    api_key: str
    model: str
    base_url: str | None = None
    thinking: bool = False
    context_window: int = 0

#上下文窗口大小
def effective_context_window(p: ProviderConfig) -> int:
    if p.context_window > 0:
        return p.context_window
    if p.protocol == "openai":
        return DEFAULT_OPENAI_CONTEXT_WINDOW
    return DEFAULT_ANTHROPIC_CONTEXT_WINDOW

#保存两个功能开关
@dataclass
class FeaturesConfig:
    coordinator_mode: bool = False
    fork_teammate: bool = False

#配置根对象
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


# ── 配置文件查找（让 uvx / uv tool 装好后在任意目录都能跑）──────────────────
def user_config_path() -> Path:
    # 用户级配置：~/.mewcode/config.yaml，任何目录下运行都生效。
    return Path.home() / ".mewcode" / "config.yaml"


def project_config_path(root: str | Path | None = None) -> Path:
    # 项目级配置：<cwd>/.mewcode/config.yaml，优先级高于用户级。
    base = Path(root) if root is not None else Path.cwd()
    return base / ".mewcode" / "config.yaml"


def search_paths(root: str | Path | None = None) -> list[Path]:
    # 按优先级返回候选路径：环境变量 > 项目级 > 用户级。
    paths: list[Path] = []
    env = os.environ.get(CONFIG_ENV)
    if env:
        paths.append(Path(env).expanduser())
    paths.append(project_config_path(root))
    paths.append(user_config_path())
    return paths


def resolve(root: str | Path | None = None) -> Path | None:
    # 返回第一个存在的配置文件；都不存在返回 None。
    # 显式设置了 MEWCODE_CONFIG 却没文件时直接报错，避免静默回退到别的配置。
    env = os.environ.get(CONFIG_ENV)
    if env:
        env_path = Path(env).expanduser()
        if not env_path.is_file():
            raise ConfigError(f"{CONFIG_ENV} 指向的配置文件不存在: {env_path}")
        return env_path
    for path in (project_config_path(root), user_config_path()):
        if path.is_file():
            return path
    return None


# 公开加载配置文件的函数
def load(path: str | Path) -> Config:
    # Load and validate a YAML config file, returning a Config.
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在: {config_path}")

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
