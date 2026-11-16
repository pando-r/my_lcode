from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from mewcode.agent import Agent
from mewcode.agent.runtime import SessionRuntime
from mewcode.command import (
    Kind,
    NopUI,
    Registry,
    SkillSummary,
    register_builtins,
    register_skill_commands,
    register_skill_management,
)
from mewcode.command.skills import handle_skill
from mewcode.conversation import Conversation
from mewcode.llm import Message, StreamEvent, ToolCall
from mewcode.permission import Mode, new_engine
from mewcode.prompt import (
    ActiveSkillEntry,
    SkillCatalogItem,
    render_active_skills_block,
    render_skills_catalog,
)
from mewcode.skills import (
    ActiveSkills,
    InstallError,
    RuntimeSkillAgent,
    SkillDef,
    SkillExecutor,
    SkillLoader,
    SkillParseError,
    SkillSource,
    install_from_url,
    parse_frontmatter,
    parse_skill_dir,
    parse_skill_file,
    parse_skill_url,
    render_body,
    substitute_arguments,
)
from mewcode.skills import install as install_mod
from mewcode.tool import Registry as ToolRegistry
from mewcode.tool.install_skill import InstallSkillTool
from mewcode.tool.load_skill import LoadSkillTool

SKILL_TEXT = """---
name: demo
description: A demo skill
mode: inline
---
Echo hello
"""


def _write_skill(root: Path, folder: str, text: str = SKILL_TEXT) -> Path:
    path = root / ".mewcode" / "skills" / folder
    path.mkdir(parents=True, exist_ok=True)
    skill_file = path / "SKILL.md"
    skill_file.write_text(text, encoding="utf-8")
    return skill_file


def _user_dir(tmp_path: Path) -> Path:
    # 隔离真实 ~/.mewcode/skills，避免测试相互影响。
    return tmp_path / "user-skills"


def _loader_with_skill(tmp_path: Path, text: str = SKILL_TEXT) -> SkillLoader:
    _write_skill(tmp_path, "demo", text)
    loader = SkillLoader(tmp_path, user_dir=_user_dir(tmp_path))
    loader.load_all()
    return loader


# ── parser ───────────────────────────────────────────────────────────────


def test_parse_frontmatter_and_body():
    meta, body = parse_frontmatter(SKILL_TEXT)
    assert meta["name"] == "demo"
    assert body.strip() == "Echo hello"


def test_parse_skill_file_fields(tmp_path):
    path = _write_skill(tmp_path, "demo")
    skill = parse_skill_file(path, source=SkillSource.PROJECT, is_directory=True)
    assert skill.name == "demo"
    assert skill.description == "A demo skill"
    assert skill.prompt_body.strip() == "Echo hello"
    assert skill.mode == "inline"
    assert skill.context == "full"
    assert skill.source_path == str(path.resolve())
    assert skill.is_directory is True
    assert skill.source is SkillSource.PROJECT


def test_parse_skill_dir_and_missing(tmp_path):
    _write_skill(tmp_path, "demo")
    skill = parse_skill_dir(tmp_path / ".mewcode" / "skills" / "demo")
    assert skill.is_directory is True
    with pytest.raises(SkillParseError):
        parse_skill_dir(tmp_path / "nope")


@pytest.mark.parametrize(
    "raw",
    [
        "no frontmatter\n",
        "---\nname: x\ndescription: y\nbody\n",
        "---\n: bad: yaml\n---\nbody\n",
        "---\n- a\n- b\n---\nbody\n",
    ],
)
def test_parse_frontmatter_errors(raw):
    with pytest.raises(SkillParseError):
        parse_frontmatter(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "---\ndescription: x\n---\nbody\n",
        "---\nname: Demo\ndescription: x\n---\nbody\n",
        "---\nname: 1bad\ndescription: x\n---\nbody\n",
        "---\nname: demo\n---\nbody\n",
    ],
)
def test_validate_meta_errors(tmp_path, raw):
    path = tmp_path / "SKILL.md"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(SkillParseError):
        parse_skill_file(path)


def test_invalid_mode_and_context_fall_back(tmp_path, caplog):
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\nname: demo\ndescription: x\nmode: weird\ncontext: strange\n---\nbody\n",
        encoding="utf-8",
    )
    skill = parse_skill_file(path)
    assert skill.mode == "inline"
    assert skill.context == "full"
    assert "未知 skill mode" in caplog.text
    assert "未知 skill context" in caplog.text


def test_fork_mode_with_context(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\nname: review\ndescription: review\nmode: fork\ncontext: recent\n"
        "model: gpt-x\n---\nbody\n",
        encoding="utf-8",
    )
    skill = parse_skill_file(path)
    assert skill.is_fork is True
    assert skill.mode == "fork"
    assert skill.context == "recent"
    assert skill.model == "gpt-x"


def test_nonexistent_file(tmp_path):
    with pytest.raises(OSError):
        parse_skill_file(tmp_path / "missing.md")


def test_substitute_arguments():
    assert substitute_arguments("run $ARGUMENTS now", "x") == "run x now"
    assert substitute_arguments("run $ARGUMENTS/$ARGUMENTS", "x") == "run x/x"
    assert substitute_arguments("no placeholder", "x") == "no placeholder"
    assert substitute_arguments("arg=$ARGUMENTS", "") == "arg="


def test_render_body_without_placeholder_appends_request():
    skill = SkillDef(name="x", description="d", prompt_body="Do the thing.")
    assert render_body(skill, "").strip() == "Do the thing."
    rendered = render_body(skill, "review auth")
    assert "Do the thing." in rendered
    assert "## User Request" in rendered
    assert "review auth" in rendered


def test_render_body_substitutes_placeholder():
    skill = SkillDef(name="x", description="d", prompt_body="Review $ARGUMENTS.")
    assert render_body(skill, "auth").strip() == "Review auth."


# ── loader ───────────────────────────────────────────────────────────────


def test_loader_loads_project_and_user(tmp_path):
    project = tmp_path / "project"
    _write_skill(project, "demo")
    user_root = tmp_path / "user-skills"
    user_root.mkdir()
    (user_root / "extra.md").write_text(
        "---\nname: extra\ndescription: user skill\n---\nuser body\n", encoding="utf-8"
    )

    loader = SkillLoader(project, user_dir=user_root)
    loader.load_all()

    assert loader.names() == ["demo", "extra"]
    assert loader.get_catalog() == [("demo", "A demo skill"), ("extra", "user skill")]
    assert loader.get_source_label("demo") == "project"
    assert loader.get_source_label("extra") == "user"
    assert loader.get_source_label("nope") == ""


def test_project_overrides_user(tmp_path):
    project = tmp_path / "project"
    _write_skill(project, "demo", SKILL_TEXT)
    user_root = tmp_path / "user-skills"
    user_root.mkdir()
    (user_root / "demo").mkdir()
    (user_root / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: user demo\n---\nuser body\n", encoding="utf-8"
    )

    loader = SkillLoader(project, user_dir=user_root)
    loader.load_all()

    skill = loader.get("demo")
    assert skill is not None
    assert skill.description == "A demo skill"
    assert loader.get_source_label("demo") == "project"


def test_loader_skips_bad_file(tmp_path, caplog):
    _write_skill(tmp_path, "demo")
    (tmp_path / ".mewcode" / "skills" / "bad.md").write_text("---\nname: BAD!\n---\n", "utf-8")
    loader = SkillLoader(tmp_path, user_dir=_user_dir(tmp_path))
    loader.load_all()
    assert loader.names() == ["demo"]
    assert "Skipping project skill 'bad.md'" in caplog.text


def test_loader_unknown_and_directory_flag(tmp_path):
    loader = _loader_with_skill(tmp_path)
    assert loader.get("nope") is None
    assert loader.get("demo").is_directory is True


def test_loader_hot_reload(tmp_path):
    path = _write_skill(tmp_path, "demo")
    loader = SkillLoader(tmp_path, user_dir=_user_dir(tmp_path))
    loader.load_all()
    assert "Echo hello" in loader.get("demo").prompt_body

    path.write_text("---\nname: demo\ndescription: A demo skill\n---\nNEW BODY\n", encoding="utf-8")
    assert "NEW BODY" in loader.get("demo").prompt_body


def test_loader_hot_reload_failure_falls_back(tmp_path, caplog):
    loader = _loader_with_skill(tmp_path)
    cached = loader.get("demo")
    _write_skill(tmp_path, "demo", "broken")

    skill = loader.get("demo")
    assert skill is not None
    assert skill.prompt_body == cached.prompt_body
    assert "回退缓存" in caplog.text


def test_loader_reload_reports_added_and_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    loader = _loader_with_skill(tmp_path)
    added, removed = loader.reload()
    assert (added, removed) == ([], [])

    _write_skill(tmp_path, "second", SKILL_TEXT.replace("demo", "second"))
    added, removed = loader.reload()
    assert added == ["second"]
    assert removed == []


# ── active state ─────────────────────────────────────────────────────────


def test_active_skills_keeps_order_and_replaces():
    active = ActiveSkills()
    active.activate("a", "A1")
    active.activate("b", "B1")
    active.activate("a", "A2")
    assert active.names() == ["a", "b"]
    assert [entry.body for entry in active.snapshot()] == ["A2", "B1"]

    snapshot = active.snapshot()
    snapshot.clear()
    assert active.names() == ["a", "b"]

    active.clear()
    assert active.names() == []


# ── prompt blocks ────────────────────────────────────────────────────────


def test_render_skills_catalog():
    assert render_skills_catalog([]) == ""
    text = render_skills_catalog([SkillCatalogItem("demo", "A demo")])
    assert "## Available Skills" in text
    assert "- demo: A demo" in text
    assert "LoadSkill" in text


def test_render_active_skills_block():
    assert render_active_skills_block([]) == ""
    text = render_active_skills_block([ActiveSkillEntry("demo", "Echo hello")])
    assert "## Active Skills" in text
    assert "### Skill: demo" in text
    assert "Echo hello" in text


# ── LoadSkill tool ───────────────────────────────────────────────────────


async def test_load_skill_tool_activates(tmp_path):
    loader = _loader_with_skill(tmp_path)
    runtime = SessionRuntime()
    tool = LoadSkillTool(loader, RuntimeSkillAgent(runtime))

    assert tool.read_only is True
    result = await tool.execute(json.dumps({"name": "demo"}))
    assert not result.is_error
    assert "demo" in result.content
    assert "SOP pinned" in result.content
    assert runtime.active_skills.names() == ["demo"]


async def test_load_skill_tool_unknown_and_uninitialized():
    tool = LoadSkillTool()
    assert (await tool.execute('{"name": "x"}')).is_error

    loader = SkillLoader(Path.cwd())
    tool = LoadSkillTool(loader, RuntimeSkillAgent(SessionRuntime()))
    result = await tool.execute('{"name": "missing"}')
    assert result.is_error
    assert "未知 Skill" in result.content


async def test_load_skill_tool_bad_json(tmp_path):
    loader = _loader_with_skill(tmp_path)
    tool = LoadSkillTool(loader, RuntimeSkillAgent(SessionRuntime()))
    assert (await tool.execute("not json")).is_error


# ── Agent integration ────────────────────────────────────────────────────


class RecordingProvider:
    def __init__(self, scripts: list[list[StreamEvent]] | None = None) -> None:
        self.scripts = scripts or [[StreamEvent(text="ok"), StreamEvent(done=True)]]
        self.requests = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        self.requests.append(req)
        index = min(len(self.requests) - 1, len(self.scripts) - 1)
        for event in self.scripts[index]:
            yield event


def _agent(provider, registry, tmp_path, runtime):
    return Agent(provider, registry, "dev", new_engine(str(tmp_path))[0], runtime=runtime)


async def test_agent_injects_catalog_and_active_skills(tmp_path):
    loader = _loader_with_skill(tmp_path)
    runtime = SessionRuntime(skill_catalog=loader)
    runtime.active_skills.activate("demo", "Echo hello")
    provider = RecordingProvider()
    conv = Conversation()
    conv.add_user("hi")

    async for _ in _agent(provider, ToolRegistry(), tmp_path, runtime).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        pass

    request = provider.requests[0]
    assert "## Available Skills" in request.system.stable
    assert "- demo: A demo skill" in request.system.stable
    assert "## Active Skills" in request.system.environment
    assert "### Skill: demo" in request.system.environment
    assert "Echo hello" in request.system.environment


async def test_agent_without_skills_has_no_blocks(tmp_path):
    provider = RecordingProvider()
    conv = Conversation()
    conv.add_user("hi")

    async for _ in _agent(provider, ToolRegistry(), tmp_path, SessionRuntime()).run(
        conv, Mode.DEFAULT, asyncio.Event()
    ):
        pass

    request = provider.requests[0]
    assert "## Available Skills" not in request.system.stable
    assert "## Active Skills" not in request.system.environment


async def test_agent_activate_and_clear_api(tmp_path):
    runtime = SessionRuntime()
    agent = _agent(RecordingProvider(), ToolRegistry(), tmp_path, runtime)
    agent.activate_skill("demo", "body")
    assert runtime.active_skills.names() == ["demo"]
    assert agent.list_active() == ["demo"]
    agent.clear_active_skills()
    assert runtime.active_skills.names() == []


async def test_agent_load_skill_mid_turn_updates_environment(tmp_path):
    loader = _loader_with_skill(tmp_path)
    runtime = SessionRuntime(skill_catalog=loader)
    registry = ToolRegistry()
    registry.register(LoadSkillTool(loader, RuntimeSkillAgent(runtime)))
    provider = RecordingProvider(
        [
            [
                StreamEvent(
                    tool_calls=[ToolCall(id="c1", name="LoadSkill", input='{"name": "demo"}')]
                ),
                StreamEvent(done=True),
            ],
            [StreamEvent(text="done"), StreamEvent(done=True)],
        ]
    )
    conv = Conversation()
    conv.add_user("use demo")

    async for _ in _agent(provider, registry, tmp_path, runtime).run(
        conv, Mode.BYPASS, asyncio.Event()
    ):
        pass

    assert len(provider.requests) == 2
    assert "## Active Skills" not in provider.requests[0].system.environment
    assert "## Active Skills" in provider.requests[1].system.environment
    assert "Echo hello" in provider.requests[1].system.environment


# ── executor ─────────────────────────────────────────────────────────────


class CapturingUI(NopUI):
    def __init__(self) -> None:
        super().__init__()
        self.cleared = 0

    def clear_active_skills(self) -> None:
        self.cleared += 1
        super().clear_active_skills()


async def test_execute_inline_injects_and_activates(tmp_path):
    loader = _loader_with_skill(tmp_path)
    runtime = SessionRuntime(skill_catalog=loader)
    executor = SkillExecutor(loader, runtime, agent=RuntimeSkillAgent(runtime))
    ui = CapturingUI()

    await executor.execute(ui, "demo")

    assert ui.injected == [("/demo", "Echo hello\n")]
    assert runtime.active_skills.names() == ["demo"]


async def test_execute_unknown_skill(tmp_path):
    loader = _loader_with_skill(tmp_path)
    executor = SkillExecutor(loader, SessionRuntime())
    ui = CapturingUI()
    await executor.execute(ui, "missing")
    assert any("未知 Skill" in error for error in ui.errors)


@pytest.mark.parametrize(
    ("context", "expected_prefix"),
    [
        ("none", None),
        ("recent", "old question"),
        ("full", "## Previous conversation summary"),
    ],
)
def test_build_fork_conversation_modes(tmp_path, context, expected_prefix):
    loader = _loader_with_skill(tmp_path)
    executor = SkillExecutor(loader, SessionRuntime())
    history = [
        Message(role="user", content="old question"),
        Message(role="assistant", content="old answer"),
    ]

    conv = executor._build_fork_conversation(history, context, "RENDERED")

    messages = conv.messages()
    assert messages[-1].role == "user"
    assert messages[-1].content == "RENDERED"
    if expected_prefix is None:
        assert len(messages) == 1
    elif expected_prefix.startswith("##"):
        assert expected_prefix in messages[0].content
        assert len(messages) == 2
    else:
        assert [m.content for m in messages[:-1]] == ["old question", "old answer"]


async def test_execute_fork_returns_result(tmp_path):
    _write_skill(
        tmp_path,
        "review",
        "---\nname: review\ndescription: review\nmode: fork\ncontext: recent\n---\n"
        "Review $ARGUMENTS\n",
    )
    loader = SkillLoader(tmp_path, user_dir=_user_dir(tmp_path))
    loader.load_all()
    provider = RecordingProvider()
    runtime = SessionRuntime(skill_catalog=loader)
    executor = SkillExecutor(
        loader, runtime, ToolRegistry(), new_engine(str(tmp_path))[0], "dev", provider=provider
    )

    skill = loader.get("review")
    text = await executor.execute_fork(
        skill, "auth", main_messages=[Message(role="user", content="old")]
    )

    assert text == "ok"
    assert "Review auth" in provider.requests[0].messages[-1].content
    assert runtime.active_skills.names() == []


async def test_execute_fork_reports_failure_without_provider(tmp_path):
    loader = _loader_with_skill(tmp_path)
    executor = SkillExecutor(loader, SessionRuntime())
    skill = SkillDef(name="forky", description="d", prompt_body="body", mode="fork", context="none")
    text = await executor.execute_fork(skill, "", main_messages=[])
    assert "provider 未就绪" in text


async def test_fork_command_writes_assistant_message(tmp_path):
    _write_skill(
        tmp_path,
        "forky",
        "---\nname: forky\ndescription: fork\nmode: fork\ncontext: none\n---\nbody\n",
    )
    loader = SkillLoader(tmp_path, user_dir=_user_dir(tmp_path))
    loader.load_all()
    provider = RecordingProvider()
    runtime = SessionRuntime(skill_catalog=loader)
    executor = SkillExecutor(
        loader, runtime, ToolRegistry(), new_engine(str(tmp_path))[0], "dev", provider=provider
    )
    ui = CapturingUI()
    ui._history = [Message(role="user", content="main")]

    await executor.execute(ui, "forky")
    for _ in range(100):
        if ui._appended:
            break
        await asyncio.sleep(0)

    assert ui._appended == ["ok"]


# ── command integration ──────────────────────────────────────────────────


def _command_registry(loader, executor):
    reg = Registry()
    register_builtins(reg)
    register_skill_management(reg, loader, executor)
    register_skill_commands(reg, loader, executor)
    return reg


def test_skill_commands_registered_and_override(tmp_path):
    loader = _loader_with_skill(tmp_path)
    executor = SkillExecutor(loader, SessionRuntime())
    reg = _command_registry(loader, executor)

    command = reg.lookup("demo")
    assert command is not None
    assert command.kind is Kind.PROMPT
    assert "[skill]" in command.description
    assert command.accepts_args is True
    assert reg.lookup("skill").accepts_args is True

    # F14：同名 skill 覆盖内置 handler。
    override_root = tmp_path / "override"
    _write_skill(
        override_root,
        "status",
        "---\nname: status\ndescription: custom status\n---\nbody\n",
    )
    override_loader = SkillLoader(override_root, user_dir=_user_dir(tmp_path))
    override_loader.load_all()
    override_reg = _command_registry(override_loader, executor)
    assert "[skill]" in override_reg.lookup("status").description


async def test_skill_list_and_info(tmp_path):
    loader = _loader_with_skill(tmp_path)
    runtime = SessionRuntime(skill_catalog=loader)
    executor = SkillExecutor(loader, runtime)
    reg = _command_registry(loader, executor)
    ui = CapturingUI()
    ui._catalog_skills = [
        SkillSummary(name="demo", description="A demo skill", source="project", mode="inline")
    ]
    ui._active_skills = ["demo"]

    await handle_skill(ui, reg, loader, executor)
    assert "demo" in ui.printed[-1]
    assert "[project]" in ui.printed[-1]
    assert "已激活: demo" in ui.printed[-1]

    ui.set_command_args("info demo")
    await handle_skill(ui, reg, loader, executor)
    text = ui.printed[-1]
    assert "name: demo" in text
    assert "path:" in text
    assert "directory: yes" in text


async def test_skill_usage_and_unknown_info(tmp_path):
    loader = _loader_with_skill(tmp_path)
    executor = SkillExecutor(loader, SessionRuntime())
    reg = _command_registry(loader, executor)
    ui = CapturingUI()

    ui.set_command_args("info")
    await handle_skill(ui, reg, loader, executor)
    assert any("用法" in error for error in ui.errors)

    ui.set_command_args("info nope")
    await handle_skill(ui, reg, loader, executor)
    assert any("未知 Skill" in error for error in ui.errors)

    ui.set_command_args("bogus")
    await handle_skill(ui, reg, loader, executor)
    assert any("用法" in error for error in ui.errors)


async def test_reload_restores_builtin_after_skill_removed(tmp_path):
    project = tmp_path / "project"
    _write_skill(
        project,
        "status",
        "---\nname: status\ndescription: custom status\n---\nbody\n",
    )
    loader = SkillLoader(project, user_dir=_user_dir(tmp_path))
    loader.load_all()
    executor = SkillExecutor(loader, SessionRuntime())
    reg = _command_registry(loader, executor)
    assert "[skill]" in reg.lookup("status").description

    shutil.rmtree(project / ".mewcode" / "skills" / "status")
    ui = CapturingUI()
    ui.set_command_args("reload")
    await handle_skill(ui, reg, loader, executor)

    restored = reg.lookup("status")
    assert restored is not None
    assert "[skill]" not in restored.description
    assert "显示当前运行状态" in restored.description


async def test_skill_reload_registers_new_commands(tmp_path):
    loader = _loader_with_skill(tmp_path)
    executor = SkillExecutor(loader, SessionRuntime())
    reg = _command_registry(loader, executor)
    ui = CapturingUI()

    _write_skill(tmp_path, "second", SKILL_TEXT.replace("demo", "second"))
    ui.set_command_args("reload")
    await handle_skill(ui, reg, loader, executor)

    assert reg.lookup("second") is not None
    assert "新增 1" in ui.printed[-1]


async def test_inline_skill_command_handler(tmp_path):
    loader = _loader_with_skill(tmp_path)
    runtime = SessionRuntime(skill_catalog=loader)
    executor = SkillExecutor(loader, runtime, agent=RuntimeSkillAgent(runtime))
    reg = _command_registry(loader, executor)
    ui = CapturingUI()
    ui.set_command_args("auth module")

    await reg.lookup("demo").handler(ui)

    assert ui.injected and ui.injected[0][0] == "/demo"
    assert "Echo hello" in ui.injected[0][1]
    assert "auth module" in ui.injected[0][1]
    assert runtime.active_skills.names() == ["demo"]


async def test_clear_command_clears_active_skills(tmp_path):
    loader = _loader_with_skill(tmp_path)
    runtime = SessionRuntime(skill_catalog=loader)
    runtime.active_skills.activate("demo", "body")
    executor = SkillExecutor(loader, runtime)
    reg = _command_registry(loader, executor)
    ui = CapturingUI()

    await reg.lookup("clear").handler(ui)

    # 具体清理动作由 UI 实现（TUI 中清空 runtime.active_skills）。
    assert ui.cleared == 1


# ── install ──────────────────────────────────────────────────────────────


def test_parse_skill_url_three_formats():
    skills_sh = parse_skill_url("https://skills.sh/anthropics/skills/frontend")
    assert (skills_sh.owner, skills_sh.repo, skills_sh.path) == (
        "anthropics",
        "skills",
        "frontend",
    )

    github = parse_skill_url("https://github.com/o/r/tree/dev/skills/x")
    assert (github.owner, github.repo, github.ref, github.path) == ("o", "r", "dev", "skills/x")

    raw = parse_skill_url("https://raw.githubusercontent.com/o/r/main/skills/x/SKILL.md")
    assert (raw.owner, raw.repo, raw.ref, raw.path) == ("o", "r", "main", "skills/x")

    raw_dir = parse_skill_url("https://raw.githubusercontent.com/o/r/main/skills/x/")
    assert raw_dir.path == "skills/x"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/o/r",
        "https://skills.sh/only-owner",
        "https://github.com/o",
        "https://raw.githubusercontent.com/o/r/main",
    ],
)
def test_parse_skill_url_rejects(url):
    with pytest.raises(InstallError):
        parse_skill_url(url)


def _install_mocks(monkeypatch, files: dict[str, bytes], sizes: dict[str, int] | None = None):
    base_sizes = sizes or {}

    async def fake_fetch(parsed, path, client):
        prefix = (parsed.path.strip("/") + "/") if parsed.path.strip("/") else ""
        return [
            {
                "type": "file",
                "path": prefix + name,
                "size": base_sizes.get(name, len(data)),
                "download_url": "https://raw.test/" + name,
            }
            for name, data in files.items()
        ]

    async def fake_download(url, client):
        return files[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(install_mod, "_fetch_contents", fake_fetch)
    monkeypatch.setattr(install_mod, "_download_file", fake_download)


async def test_install_from_url_success(tmp_path, monkeypatch):
    _install_mocks(monkeypatch, {"SKILL.md": SKILL_TEXT.encode("utf-8")})
    root = tmp_path / "user-skills"

    name = await install_from_url("https://skills.sh/o/r/demo", install_root=root, client=object())

    assert name == "demo"
    assert (root / "demo" / "SKILL.md").is_file()
    assert not list(root.glob(".skill-install-*"))


async def test_install_from_url_missing_skill_md(tmp_path, monkeypatch):
    _install_mocks(monkeypatch, {"README.md": b"hello"})
    root = tmp_path / "user-skills"

    with pytest.raises(InstallError):
        await install_from_url("https://skills.sh/o/r/demo", install_root=root, client=object())

    assert list(root.glob(".skill-install-*")) == []
    assert not (root / "demo").exists()


async def test_install_from_url_rejects_oversized_file(tmp_path, monkeypatch):
    _install_mocks(
        monkeypatch,
        {"SKILL.md": SKILL_TEXT.encode("utf-8")},
        sizes={"SKILL.md": install_mod.MAX_FILE_SIZE + 1},
    )
    root = tmp_path / "user-skills"

    with pytest.raises(InstallError):
        await install_from_url("https://skills.sh/o/r/demo", install_root=root, client=object())


async def test_install_from_url_rejects_traversal(tmp_path, monkeypatch):
    async def fake_fetch(parsed, path, client):
        return [
            {
                "type": "file",
                "path": "../evil.md",
                "size": 1,
                "download_url": "https://raw.test/x",
            }
        ]

    monkeypatch.setattr(install_mod, "_fetch_contents", fake_fetch)
    root = tmp_path / "user-skills"

    with pytest.raises(InstallError):
        await install_from_url("https://skills.sh/o/r/demo", install_root=root, client=object())


async def test_install_from_url_duplicate(tmp_path, monkeypatch):
    _install_mocks(monkeypatch, {"SKILL.md": SKILL_TEXT.encode("utf-8")})
    root = tmp_path / "user-skills"
    (root / "demo").mkdir(parents=True)

    with pytest.raises(InstallError):
        await install_from_url("https://skills.sh/o/r/demo", install_root=root, client=object())


async def test_install_skill_tool_reloads_and_calls_back(tmp_path, monkeypatch):
    _install_mocks(monkeypatch, {"SKILL.md": SKILL_TEXT.encode("utf-8")})
    loader = SkillLoader(tmp_path, user_dir=_user_dir(tmp_path))
    loader.load_all()
    calls = []
    tool = InstallSkillTool(loader, on_installed=lambda: calls.append("yes"))

    result = await tool.execute(json.dumps({"source": "https://skills.sh/o/r/demo"}))

    assert not result.is_error
    assert "demo" in result.content
    assert (loader.user_dir / "demo" / "SKILL.md").is_file()
    assert loader.get("demo") is not None
    assert calls == ["yes"]
    assert tool.read_only is False
    assert tool.name() == "InstallSkill"


async def test_install_skill_tool_reports_error(tmp_path, monkeypatch):
    async def boom(source, install_root=None, client=None):
        raise InstallError("nope")

    monkeypatch.setattr("mewcode.tool.install_skill.install_from_url", boom)
    loader = SkillLoader(tmp_path, user_dir=_user_dir(tmp_path))
    tool = InstallSkillTool(loader, install_root=_user_dir(tmp_path))

    result = await tool.execute(json.dumps({"source": "https://example.com/x"}))
    assert result.is_error
    assert "安装失败" in result.content
