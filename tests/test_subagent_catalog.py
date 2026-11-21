from __future__ import annotations

from pathlib import Path

from lcode.subagent import Source, load_catalog
from lcode.subagent.catalog import FORK_AGENT_NAME


def _write(path: Path, name: str, description: str = "d", body: str = "body") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )


def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def test_builtin_definitions(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    catalog = load_catalog(tmp_path)
    names = {definition.name for definition in catalog.list()}
    assert names == {"general-purpose", "Explore", "Plan"}
    assert catalog.resolve("Explore").source is Source.BUILTIN
    assert catalog.resolve("Explore").model == "haiku"
    assert catalog.resolve("Plan").permission_mode.name == "PLAN"
    assert catalog.list_by_source(Source.BUILTIN)


def test_project_overrides_user_overrides_builtin(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write(home / ".lcode" / "agents" / "explore.md", "Explore", "user version")
    _write(tmp_path / ".lcode" / "agents" / "explore.md", "Explore", "project version")

    catalog = load_catalog(tmp_path)
    definition = catalog.resolve("Explore")
    assert definition is not None
    assert definition.source is Source.PROJECT
    assert definition.description == "project version"

    # 用户级定义在 _by_source 里仍然可见
    user_defs = catalog.list_by_source(Source.USER)
    assert [d.source for d in user_defs] == [Source.USER]


def test_user_overrides_builtin(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write(home / ".lcode" / "agents" / "explore.md", "Explore", "user version")
    catalog = load_catalog(tmp_path)
    assert catalog.resolve("Explore").source is Source.USER


def test_project_custom_agent_loaded(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    _write(
        tmp_path / ".lcode" / "agents" / "wc-counter.md",
        "wc-counter",
        "count lines",
    )
    catalog = load_catalog(tmp_path)
    definition = catalog.resolve("wc-counter")
    assert definition is not None
    assert definition.source is Source.PROJECT


def test_bad_definition_is_skipped(tmp_path, monkeypatch, capsys):
    _isolate_home(tmp_path, monkeypatch)
    agents = tmp_path / ".lcode" / "agents"
    agents.mkdir(parents=True)
    (agents / "broken.md").write_text("not frontmatter", encoding="utf-8")
    _write(agents / "good.md", "good", "good role")

    catalog = load_catalog(tmp_path)
    names = {definition.name for definition in catalog.list()}
    assert "good" in names
    assert "broken" not in names
    assert "skipped" in capsys.readouterr().err


def test_fork_definition(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    fork = load_catalog(tmp_path).fork_definition()
    assert fork.name == FORK_AGENT_NAME
    assert fork.is_fork() is True
    assert fork.system_prompt == ""
    assert "Agent" not in fork.disallowed_tools
