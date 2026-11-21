from __future__ import annotations

import asyncio

from lcode.agent import ApprovalRequest
from lcode.config import ProviderConfig
from lcode.permission import Outcome, new_engine
from lcode.subagent import load_catalog
from lcode.task import Manager
from lcode.tool import new_default_registry
from lcode.tui.app import LcodeApp, SessionState


class TaskAgent:
    def __init__(self, result: str = "bg result") -> None:
        self.result = result

    async def run_to_completion(self, conv, task="", events=None):
        return self.result


def _app(tmp_path, task_mgr=None) -> LcodeApp:
    providers = [
        ProviderConfig(name="fake", protocol="openai", api_key="test-key", model="fake-model")
    ]
    return LcodeApp(
        providers,
        "dev",
        new_default_registry(),
        new_engine(str(tmp_path))[0],
        workspace=str(tmp_path),
        context_window=128000,
        task_mgr=task_mgr,
        subagent_catalog=load_catalog(str(tmp_path)),
    )


async def test_task_completion_injects_notification(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    manager = Manager()
    app = _app(tmp_path, manager)
    async with app.run_test() as pilot:
        await manager.launch(TaskAgent("done!"), app.conv, "worker", "work")
        for _ in range(100):
            if app.runtime.pending_reminders:
                break
            await pilot.pause()

        reminders = app.runtime.take_reminders()
        assert reminders
        text = reminders[0]
        assert "<task-notification>" in text
        assert 'name="worker"' in text
        assert "completed" in text
        assert "Result: done!" in text
        await app.action_quit()


async def test_subagent_approval_upgrades_to_tui(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    manager = Manager()
    app = _app(tmp_path, manager)
    async with app.run_test() as pilot:
        assert manager._approval_handler is not None
        req = ApprovalRequest(name="bash", args="echo hi", reason="需要审批")
        pending = asyncio.create_task(app._upgrade_subagent_approval(req))
        await pilot.pause()

        assert app.state is SessionState.APPROVING
        assert app.pending is not None
        assert app.pending.reason.startswith("[来自 SubAgent]")

        app._submit_approval(0)
        outcome = await asyncio.wait_for(pending, timeout=2)
        assert outcome is Outcome.ALLOW_ONCE
        assert app.state is SessionState.IDLE

        await app.action_quit()


async def test_consume_task_done_without_manager_is_inert(tmp_path):
    app = _app(tmp_path)
    async with app.run_test():
        assert app._task_done_task is None
        await app.action_quit()
