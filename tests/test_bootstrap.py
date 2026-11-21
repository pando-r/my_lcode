"""配置查找优先级与首次运行引导。"""

from __future__ import annotations

import pytest

from lcode import bootstrap
from lcode import config as config_mod
from lcode.config import CONFIG_ENV, ConfigError, resolve


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    # 把用户级配置固定到临时目录，避免碰到真实 ~/.lcode
    home_cfg = tmp_path / "home" / ".lcode" / "config.yaml"
    monkeypatch.setattr(config_mod, "user_config_path", lambda: home_cfg)
    monkeypatch.setattr(
        config_mod,
        "project_config_path",
        lambda root=None: tmp_path / "proj" / ".lcode" / "config.yaml",
    )
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    return home_cfg


def _write(path, text="providers:\n  - {name: A, protocol: anthropic, api_key: k, model: m}\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_resolve_none_when_nothing_exists(fake_home):
    assert resolve() is None


def test_resolve_falls_back_to_user_config(fake_home):
    _write(fake_home)
    assert resolve() == fake_home


def test_project_config_wins_over_user(fake_home, tmp_path):
    _write(fake_home)
    project = _write(tmp_path / "proj" / ".lcode" / "config.yaml")
    assert resolve() == project


def test_env_var_wins(fake_home, tmp_path, monkeypatch):
    _write(fake_home)
    project = _write(tmp_path / "proj" / ".lcode" / "config.yaml")
    explicit = _write(tmp_path / "custom.yaml")
    monkeypatch.setenv(CONFIG_ENV, str(explicit))
    assert resolve() == explicit
    assert resolve() != project


def test_env_var_missing_file_raises(fake_home, tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_ENV, str(tmp_path / "nope.yaml"))
    with pytest.raises(ConfigError, match=CONFIG_ENV):
        resolve()


def test_ensure_user_config_creates_and_keeps(fake_home):
    path, created = bootstrap.ensure_user_config()
    assert created is True
    assert path == fake_home
    assert "providers:" in path.read_text(encoding="utf-8")
    # 已存在时不覆盖用户改动
    path.write_text("providers: [{name: mine}]\n", encoding="utf-8")
    _, created_again = bootstrap.ensure_user_config()
    assert created_again is False
    assert "mine" in path.read_text(encoding="utf-8")


def test_ensure_user_config_overwrite(fake_home):
    _write(fake_home, "providers: [{name: mine}]\n")
    path, created = bootstrap.ensure_user_config(overwrite=True)
    assert created is True
    assert "api_key" in path.read_text(encoding="utf-8")


def test_init_only_generates_template_when_missing(fake_home):
    from lcode.cli import STATUS_INIT, _prepare_config

    path, status = _prepare_config(None, init_only=True)
    assert path is None
    assert status == STATUS_INIT
    assert fake_home.is_file()


def test_init_only_exits_when_config_already_exists(fake_home):
    """--init 语义是「生成模板后退出」；配置已存在时必须退出，不能继续启动 TUI。

    安装脚本最后一步会调 `lcode --init`，这里若退化成启动界面，
    已装过的人重跑安装器就会被丢进全屏 TUI，看着像卡死。
    """
    from lcode.cli import STATUS_INIT, _prepare_config

    _write(fake_home, "providers: [{name: mine}]\n")
    path, status = _prepare_config(None, init_only=True)
    assert path is None
    assert status == STATUS_INIT
    # 用户已有的配置不被覆盖
    assert "mine" in fake_home.read_text(encoding="utf-8")

