"""Configuration loading and validation for lcode."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

# 显式指定配置文件路径的环境变量（优先级最高）
CONFIG_ENV = "LCODE_CONFIG"

# 配置值里的 ${VAR} 会从环境变量展开（密钥不必写进文件）。
# 与 MCP 配置同一套语法；未定义的变量展开为空串，由调用方的必填校验兜住。
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# 目前支持的两种LLM协议类型
VALID_PROTOCOLS = ("anthropic", "openai")

# 协议默认上下文窗口（token），未配置 context_window 时按此回退
DEFAULT_ANTHROPIC_CONTEXT_WINDOW = 200000
DEFAULT_OPENAI_CONTEXT_WINDOW = 128000


# LLM服务商的配置项
@dataclass
class ProviderConfig:
    name: str
    protocol: Literal["anthropic", "openai"]
    api_key: str
    model: str
    base_url: str | None = None
    thinking: bool = False
    context_window: int = 0


# 「眼睛」副模型：主模型保持纯文本，图片先被它翻译成文字再进对话。
# 单独成段而不是塞进 providers，是因为 providers 列表决定启动时的选择菜单
# （见 tui/app.py 的 len(providers) == 1 分支），多一个会平白弹出选项。
@dataclass
class VisionConfig:
    protocol: Literal["anthropic", "openai"] = "openai"
    api_key: str = ""
    model: str = ""
    base_url: str | None = None
    max_image_side: int = 1568  # Claude 的长边上限，超过也基本只按 1568 处理
    detail: str = "high"  # OpenAI 协议的 detail 参数：low / high / auto
    timeout: float = 120.0

    def ready(self) -> bool:
        return bool(self.api_key and self.model)


# 语音转写：和 vision 一样是「副服务」。backend=openai 走兼容
# /audio/transcriptions 的云端接口；backend=local 用 faster-whisper（不联网）。
@dataclass
class AudioConfig:
    # dashscope：百炼原生 multimodal-generation（qwen3-asr-flash 等）
    # openai：OpenAI 兼容的 /audio/transcriptions
    # local：faster-whisper，不联网
    backend: Literal["dashscope", "openai", "local"] = "dashscope"
    api_key: str = ""
    model: str = "qwen3-asr-flash"
    base_url: str | None = None
    language: str = "zh"
    local_model: str = "small"  # 仅 backend=local 时生效
    # ITN（逆文本规范化）：把「八千七百一十四」写成 8714。
    # 对接代码/数字场景更实用，但转写文学文本时可关掉保留原话。
    itn: bool = True
    timeout: float = 600.0

    def ready(self) -> bool:
        if self.backend == "local":
            return True  # 本地后端不需要 key，缺依赖会在调用时报错
        return bool(self.api_key and self.model)


# 上下文窗口大小
def effective_context_window(p: ProviderConfig) -> int:
    if p.context_window > 0:
        return p.context_window
    if p.protocol == "openai":
        return DEFAULT_OPENAI_CONTEXT_WINDOW
    return DEFAULT_ANTHROPIC_CONTEXT_WINDOW


# 保存两个功能开关
@dataclass
class FeaturesConfig:
    coordinator_mode: bool = False
    fork_teammate: bool = False


# 配置根对象
@dataclass
class Config:
    providers: list[ProviderConfig] = field(default_factory=list)
    enable_subagent_background: bool | None = None
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    vision: VisionConfig | None = None
    audio: AudioConfig | None = None

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
    # 用户级配置：~/.lcode/config.yaml，任何目录下运行都生效。
    return Path.home() / ".lcode" / "config.yaml"


def project_config_path(root: str | Path | None = None) -> Path:
    # 项目级配置：<cwd>/.lcode/config.yaml，优先级高于用户级。
    base = Path(root) if root is not None else Path.cwd()
    return base / ".lcode" / "config.yaml"


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
    # 显式设置了 LCODE_CONFIG 却没文件时直接报错，避免静默回退到别的配置。
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

        # 先展开 ${VAR} 再校验：否则写错变量名会被当成「有值」而静默失效。
        required = ("name", "protocol", "api_key", "model")
        values = {name: _secret(item.get(name)) for name in required}
        for field_name, value in values.items():
            if not value:
                raise ConfigError(
                    f"{label}.{field_name} 必须是非空字符串"
                    f"（若用 ${{VAR}} 引用环境变量，请确认该变量已设置）"
                )

        protocol = values["protocol"]
        if protocol not in VALID_PROTOCOLS:
            raise ConfigError(
                f"{label}.protocol 必须是 {' / '.join(VALID_PROTOCOLS)} 之一，实际为: {protocol!r}"
            )

        result.append(
            ProviderConfig(
                name=values["name"],
                protocol=protocol,
                api_key=values["api_key"],
                model=values["model"],
                base_url=_optional_expanded(item.get("base_url")),
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
        vision=_parse_vision(raw.get("vision")),
        audio=_parse_audio(raw.get("audio")),
    )


def _parse_vision(raw: object) -> VisionConfig | None:
    # 显式写了 vision 段就要求写全，缺字段直接报错——静默降级会让
    # 「配了但没生效」变成最难查的一类问题。
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("vision 必须是映射（protocol / api_key / model）")

    protocol = str(raw.get("protocol") or "openai").strip()
    if protocol not in VALID_PROTOCOLS:
        raise ConfigError(
            f"vision.protocol 必须是 {' / '.join(VALID_PROTOCOLS)} 之一，实际为: {protocol!r}"
        )

    api_key = _secret(raw.get("api_key"))
    model = _secret(raw.get("model"))
    missing = [name for name, value in (("api_key", api_key), ("model", model)) if not value]
    if missing:
        joined = " / ".join(f"vision.{name}" for name in missing)
        raise ConfigError(
            f"{joined} 必须是非空字符串（若用 ${{VAR}}，请确认环境变量已设置）"
        )

    return VisionConfig(
        protocol=protocol,  # type: ignore[arg-type]  # 上面已校验过取值
        api_key=api_key,
        model=model,
        base_url=_optional_expanded(raw.get("base_url")),
        max_image_side=_int_optional(raw.get("max_image_side")) or 1568,
        detail=str(raw.get("detail") or "high").strip() or "high",
        timeout=_float_optional(raw.get("timeout")) or 120.0,
    )


def _parse_audio(raw: object) -> AudioConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("audio 必须是映射（backend / api_key / model）")

    backend = str(raw.get("backend") or "dashscope").strip().lower()
    if backend not in ("dashscope", "openai", "local"):
        raise ConfigError(
            f"audio.backend 必须是 dashscope / openai / local 之一，实际为: {backend!r}"
        )

    cfg = AudioConfig(
        backend=backend,  # type: ignore[arg-type]  # 上面已校验过取值
        api_key=_secret(raw.get("api_key")),
        model=_secret(raw.get("model")) or "qwen3-asr-flash",
        base_url=_optional_expanded(raw.get("base_url")),
        language=_optional_str(raw.get("language")) or "zh",
        local_model=_optional_str(raw.get("local_model")) or "small",
        itn=_optional_bool(raw.get("itn")) if raw.get("itn") is not None else True,
        timeout=_float_optional(raw.get("timeout")) or 600.0,
    )
    # 云端后端缺 key 基本等于「配了但用不了」，启动时就报出来；
    # local 后端由 faster-whisper 的 import 结果说话，不在配置期强求。
    if cfg.backend in ("dashscope", "openai") and not cfg.api_key:
        raise ConfigError(
            "audio.api_key 必须是非空字符串（或把 audio.backend 改成 local；"
            "若用 ${VAR}，请确认环境变量已设置）"
        )
    return cfg


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


def expand_vars(text: str) -> str:
    """Expand ${VAR} from the environment; undefined vars collapse to ""."""
    return _VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), text)


def _secret(value: object) -> str:
    # 密钥类字段：先展开变量再判空，避免 ${TYPO} 变成一个「看起来有值」的空串。
    if value is None:
        return ""
    return expand_vars(str(value)).strip()


def _optional_expanded(value: object) -> str | None:
    # base_url 这类可选字段：展开后再判空，${VAR} 未定义时视为未设置。
    text = _secret(value)
    return text or None


def _int_optional(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _float_optional(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return 0.0
    return 0.0
