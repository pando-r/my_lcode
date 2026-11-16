from __future__ import annotations

from mewcode.agent.launch import ForkLaunchOpts, launch_fork
from mewcode.agent.runtime import SessionRuntime
from mewcode.conversation import Conversation
from mewcode.llm import StreamEvent
from mewcode.permission import new_engine
from mewcode.skills import SkillExecutor, SkillLoader
from mewcode.skills.parser import SkillDef
from mewcode.tool import Registry


class FakeProvider:
    def __init__(self, text: str = "ok") -> None:
        self.text = text
        self.received = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req):
        self.received.append(req)
        yield StreamEvent(text=self.text)
        yield StreamEvent(done=True)


async def test_launch_fork_runs_to_completion(tmp_path):
    conv = Conversation()
    conv.add_user("fork task")
    provider = FakeProvider("fork result")
    text = await launch_fork(
        ForkLaunchOpts(
            conv=conv,
            provider=provider,
            registry=Registry(),
            version="dev",
            engine=new_engine(str(tmp_path))[0],
            runtime=SessionRuntime(),
            system_prompt="FORK SYSTEM",
        )
    )
    assert text == "fork result"
    assert provider.received[0].system.stable == "FORK SYSTEM"


async def test_skill_fork_delegates_to_launch_fork(tmp_path, monkeypatch):
    skill = SkillDef(
        name="review",
        description="review",
        prompt_body="BODY $ARGUMENTS",
        mode="fork",
        context="none",
    )
    executor = SkillExecutor(
        SkillLoader(str(tmp_path)),
        SessionRuntime(),
        registry=Registry(),
        engine=new_engine(str(tmp_path))[0],
        version="dev",
        provider=FakeProvider(),
    )
    captured = {}

    async def fake_launch(opts):
        captured["opts"] = opts
        return "forked text"

    monkeypatch.setattr("mewcode.agent.launch.launch_fork", fake_launch)
    text = await executor.execute_fork(skill, "args", main_messages=[])

    assert text == "forked text"
    opts = captured["opts"]
    assert opts.conv.messages()[-1].content == "BODY args"
    assert opts.system_prompt is None
    assert opts.provider is executor._provider
