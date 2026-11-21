from __future__ import annotations

from lcode.config import ProviderConfig
from lcode.permission import Mode, new_engine
from lcode.team import Manager
from lcode.tool import new_default_registry
from lcode.tui.app import LcodeApp
from lcode.tui.view import render_statusbar


class ScriptedProvider:
    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake-model"

    async def stream(self, req):
        from lcode.llm import StreamEvent

        yield StreamEvent(text="ok")
        yield StreamEvent(done=True)


def _app(repo, team_mgr=None, coordinator_mode=False) -> LcodeApp:
    providers = [ProviderConfig(name="fake", protocol="openai", api_key="k", model="m")]
    return LcodeApp(
        providers,
        "dev",
        new_default_registry(),
        new_engine(str(repo))[0],
        workspace=str(repo),
        context_window=128000,
        team_mgr=team_mgr,
        coordinator_mode=coordinator_mode,
    )


def _capture(app):
    captured = []

    def flush():
        captured.extend(app._pending_println)
        app._pending_println = []

    app.flush_prints = flush
    return captured


async def test_team_commands(tmp_path, git_repo):
    team_mgr = Manager(tmp_path / "home", git_repo)
    await team_mgr.create("demo")
    app = _app(git_repo, team_mgr)
    async with app.run_test():
        captured = _capture(app)
        await app.dispatch_slash("/team list")
        text = "\n".join(msg for _, msg in captured)
        assert "demo" in text and "in-process" in text

        captured.clear()
        await app.dispatch_slash("/team info demo")
        text = "\n".join(msg for _, msg in captured)
        assert "Team: demo" in text
        assert "lead" in text

        captured.clear()
        await app.dispatch_slash("/team delete demo --force")
        text = "\n".join(msg for _, msg in captured)
        assert "Team 已删除: demo" in text
        assert team_mgr.get("demo") is None
        await app.action_quit()


async def test_team_accessor_absent(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        assert app.team_accessor() is None
        captured = _capture(app)
        await app.dispatch_slash("/team list")
        assert any("未启用" in msg for _, msg in captured)
        await app.action_quit()


async def test_coordinator_mode_removes_write_tools(tmp_path):
    class RecordingProvider:
        def __init__(self):
            self.tools = []

        @property
        def name(self):
            return "fake"

        @property
        def model(self):
            return "m"

        async def stream(self, req):
            from lcode.llm import StreamEvent

            self.tools = [tool.name for tool in req.tools]
            yield StreamEvent(text="ok")
            yield StreamEvent(done=True)

    app = _app(tmp_path, coordinator_mode=True)
    async with app.run_test() as pilot:
        provider = RecordingProvider()
        app.provider = provider
        await app.submit("hi")
        for _ in range(100):
            if app.idle() and provider.tools:
                break
            await pilot.pause()
        assert "bash" in provider.tools
        assert "read_file" in provider.tools
        assert "write_file" not in provider.tools
        assert "edit_file" not in provider.tools
        await app.action_quit()


def test_build_team_update_reminder_truncates():
    from lcode.team import LeadMessage
    from lcode.tui.tasks import build_team_update_reminder

    message = LeadMessage(
        team_name="demo",
        from_="alice",
        type="text",
        content="x" * 9000,
        timestamp="t",
    )
    reminder = build_team_update_reminder([message])
    assert "<team-update>" in reminder
    assert "team=demo" in reminder
    assert "truncated" in reminder


def test_statusbar_coordinator_label():
    from rich.console import Console

    console = Console(record=True, width=120)
    console.print(render_statusbar(Mode.DEFAULT, "m", coordinator=True))
    assert "[COORDINATOR]" in console.export_text()
