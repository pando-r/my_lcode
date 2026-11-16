"""MCP server configuration: two-layer merge, ${VAR} expansion, validation."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class ServerConfig:
    type: Literal["stdio", "http"]
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    servers: dict[str, ServerConfig] = field(default_factory=dict)


# 中间态
@dataclass
class _RawServer:
    type: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


# 模块入口
def load_config(root: str) -> Config:
    # Load and merge user/project MCP server configs. Never raises.
    user_path = _user_path()  # 用户配置文件路径
    project_path = Path(root) / ".mewcode.yaml"  # 项目配置文件路径

    user = _load_file(user_path)
    project = _load_file(project_path)

    # 变量展开
    for name, srv in {**user, **project}.items():
        _apply_expansion(name, srv)
    # 合并配置
    merged = _merge_servers(user, project)
    # 校验配置
    servers: dict[str, ServerConfig] = {}
    for name, srv in merged.items():
        validated = _validate_server(name, srv)
        if validated is not None:
            servers[name] = validated
    return Config(servers=servers)


def _user_path() -> Path:
    try:
        return Path.home() / ".mewcode" / "config.yaml"
    except Exception:  # noqa: BLE001
        return Path("/nonexistent-user-config.yaml")


def _load_file(path: Path) -> dict[str, _RawServer]:
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        print(f"[mcp] warn: load {path} failed: {exc}", file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        return {}
    mcp_servers = raw.get("mcp_servers")
    if not isinstance(mcp_servers, dict):
        return {}
    result: dict[str, _RawServer] = {}
    for name, value in mcp_servers.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            continue
        result[name] = _RawServer(
            type=value.get("type") if isinstance(value.get("type"), str) else None,
            command=value.get("command") if isinstance(value.get("command"), str) else None,
            args=[str(x) for x in value["args"]] if isinstance(value.get("args"), list) else [],
            env=_str_map(value.get("env")),
            url=value.get("url") if isinstance(value.get("url"), str) else None,
            headers=_str_map(value.get("headers")),
        )
    return result


def _str_map(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def _apply_expansion(name: str, srv: _RawServer) -> None:
    warned: set[str] = set()
    for mapping in (srv.env, srv.headers):
        for key in list(mapping.keys()):
            value = mapping[key]
            expanded, undefined = _expand_vars(value)
            mapping[key] = expanded
            for var in undefined:
                if var not in warned:
                    warned.add(var)
                    print(
                        f"[mcp] warn: undefined env var ${{{var}}} referenced by server {name}",
                        file=sys.stderr,
                    )


def _expand_vars(s: str) -> tuple[str, list[str]]:
    undefined: list[str] = []

    def repl(match: re.Match) -> str:
        var = match.group(1)
        if var in os.environ:
            return os.environ[var]
        undefined.append(var)
        return ""

    return _VAR_RE.sub(repl, s), undefined


def _merge_servers(
    user: dict[str, _RawServer], project: dict[str, _RawServer]
) -> dict[str, _RawServer]:
    merged = dict(user)
    merged.update(project)
    return merged


def _validate_server(name: str, srv: _RawServer) -> ServerConfig | None:
    if srv.type not in ("stdio", "http"):
        print(f"[mcp] warn: skip server {name}: invalid or missing type", file=sys.stderr)
        return None
    if srv.type == "stdio":
        if not srv.command:
            print(f"[mcp] warn: skip server {name}: stdio requires command", file=sys.stderr)
            return None
        return ServerConfig(type="stdio", command=srv.command, args=srv.args, env=srv.env)
    # http
    if not srv.url:
        print(f"[mcp] warn: skip server {name}: http requires url", file=sys.stderr)
        return None
    return ServerConfig(type="http", url=srv.url, headers=srv.headers)
