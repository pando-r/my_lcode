from __future__ import annotations

import yaml

from mewcode.mcp.config import load_config


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "no-home"))
    cfg = load_config(str(tmp_path))
    assert cfg.servers == {}


def test_two_layer_merge(tmp_path, monkeypatch):
    # user layer at ~/.mewcode/config.yaml, project layer at root/.mewcode.yaml
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    _write(
        tmp_path / ".mewcode" / "config.yaml",
        {
            "mcp_servers": {
                "a": {"type": "stdio", "command": "user-a"},
                "b": {"type": "stdio", "command": "user-b"},
            }
        },
    )
    _write(
        tmp_path / ".mewcode.yaml",
        {"mcp_servers": {"b": {"type": "stdio", "command": "project-b"}}},
    )
    cfg = load_config(str(tmp_path))
    assert cfg.servers["a"].command == "user-a"
    assert cfg.servers["b"].command == "project-b"  # project overrides user


def test_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN", "secret123")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    _write(
        tmp_path / ".mewcode.yaml",
        {
            "mcp_servers": {
                "s": {
                    "type": "http",
                    "url": "http://x",
                    "headers": {"Authorization": "Bearer ${TOKEN}"},
                }
            }
        },
    )
    cfg = load_config(str(tmp_path))
    assert cfg.servers["s"].headers["Authorization"] == "Bearer secret123"


def test_command_and_args_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("PY", "C:/venv/python.exe")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    _write(
        tmp_path / ".mewcode.yaml",
        {
            "mcp_servers": {
                "s": {
                    "type": "stdio",
                    "command": "${PY}",
                    "args": ["-m", "pkg.${PY}"],
                }
            }
        },
    )
    cfg = load_config(str(tmp_path))
    assert cfg.servers["s"].command == "C:/venv/python.exe"
    assert cfg.servers["s"].args == ["-m", "pkg.C:/venv/python.exe"]


def test_undefined_var_expands_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MISSING", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    _write(
        tmp_path / ".mewcode.yaml",
        {"mcp_servers": {"s": {"type": "http", "url": "http://x", "headers": {"X": "${MISSING}"}}}},
    )
    cfg = load_config(str(tmp_path))
    assert cfg.servers["s"].headers["X"] == ""
    assert "MISSING" in capsys.readouterr().err


def test_validation_skip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    _write(
        tmp_path / ".mewcode.yaml",
        {
            "mcp_servers": {
                "no_type": {"command": "x"},
                "bad_type": {"type": "sse", "url": "http://x"},
                "stdio_no_cmd": {"type": "stdio"},
                "http_no_url": {"type": "http"},
                "ok": {"type": "stdio", "command": "echo"},
            }
        },
    )
    cfg = load_config(str(tmp_path))
    assert list(cfg.servers.keys()) == ["ok"]
    assert "skip server" in capsys.readouterr().err


def test_invalid_yaml_degraded(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / ".mewcode.yaml").write_text("invalid: [unclosed\n", encoding="utf-8")
    cfg = load_config(str(tmp_path))
    assert cfg.servers == {}
    assert "warn" in capsys.readouterr().err
