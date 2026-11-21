from __future__ import annotations

import asyncio

from lcode.hook import Engine, Event
from lcode.hook.executor import ExecutionResult
from lcode.hook.rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    PromptAction,
    Rule,
)


class FakeExecutor:
    def __init__(self, outcomes: dict[str, ExecutionResult] | None = None) -> None:
        self.calls: list[tuple[str, dict, bool]] = []
        self.outcomes = outcomes or {}
        self.closed = False

    async def run(self, rule: Rule, payload: dict, *, blocking: bool) -> ExecutionResult:
        self.calls.append((rule.name, dict(payload), blocking))
        return self.outcomes.get(rule.name, ExecutionResult())

    async def aclose(self) -> None:
        self.closed = True


def _rule(
    name: str,
    event: Event,
    text: str = "note",
    *,
    only_once: bool = False,
    async_mode: bool = False,
    condition: Condition | None = None,
) -> Rule:
    return Rule(
        name=name,
        event=event,
        action=Action(type=ActionType.PROMPT, prompt=PromptAction(text)),
        condition=condition,
        only_once=only_once,
        asyncio_mode=async_mode,
    )


async def test_dispatch_filters_event_and_condition():
    keep = _rule("keep", Event.PRE_TOOL_USE)
    other_event = _rule("other", Event.POST_TOOL_USE)
    engine = Engine([keep, other_event], ["src"])
    fake = FakeExecutor()
    engine._executor = fake

    result = await engine.dispatch(Event.PRE_TOOL_USE, {"tool_name": "Write"})
    assert result.blocked is False
    assert [call[0] for call in fake.calls] == ["keep"]
    assert fake.calls[0][1] == {"tool_name": "Write"}
    assert fake.calls[0][2] is True
    assert engine.sources == ["src"]


async def test_condition_can_skip_rule():
    from lcode.permission.matcher import ExactMatcher

    condition = Condition(CombineMode.ALL_OF, [AtomCondition("tool_name", ExactMatcher("Bash"))])
    rule = _rule("bash-only", Event.PRE_TOOL_USE, condition=condition)
    engine = Engine([rule], [])
    fake = FakeExecutor()
    engine._executor = fake

    await engine.dispatch(Event.PRE_TOOL_USE, {"tool_name": "Write"})
    assert fake.calls == []
    await engine.dispatch(Event.PRE_TOOL_USE, {"tool_name": "Bash"})
    assert [call[0] for call in fake.calls] == ["bash-only"]


async def test_prompt_action_is_collected():
    engine = Engine([_rule("note", Event.STOP, "remember this")], [])
    engine._executor = FakeExecutor({"note": ExecutionResult(prompt="remember this")})
    result = await engine.dispatch(Event.STOP, {})
    assert result.injected_prompts == ["remember this"]


async def test_blocking_rule_sets_result_and_stops_dispatch():
    engine = Engine(
        [
            _rule("first", Event.PRE_TOOL_USE, only_once=False),
            _rule("second", Event.PRE_TOOL_USE),
        ],
        [],
    )
    fake = FakeExecutor(
        {
            "first": ExecutionResult(blocked=True, reason="not allowed"),
            "second": ExecutionResult(blocked=True, reason="never"),
        }
    )
    engine._executor = fake
    result = await engine.dispatch(Event.PRE_TOOL_USE, {})
    assert result.blocked is True
    assert result.reason == "not allowed"
    assert result.blocking_hook_name == "first"
    assert [call[0] for call in fake.calls] == ["first"]  # 拦截后不再执行后续 hook


async def test_blocking_outcome_ignored_for_non_blocking_event():
    engine = Engine([_rule("n", Event.STOP)], [])
    engine._executor = FakeExecutor({"n": ExecutionResult(blocked=True, reason="x")})
    result = await engine.dispatch(Event.STOP, {})
    assert result.blocked is False


async def test_only_once_claims_and_resets():
    engine = Engine([_rule("once", Event.STOP, only_once=True)], [])
    fake = FakeExecutor()
    engine._executor = fake
    await engine.dispatch(Event.STOP, {})
    await engine.dispatch(Event.STOP, {})
    assert [call[0] for call in fake.calls] == ["once"]

    engine.reset_for_new_session()
    await engine.dispatch(Event.STOP, {})
    assert [call[0] for call in fake.calls] == ["once", "once"]


async def test_only_once_released_after_failure():
    rule = _rule("once", Event.STOP, only_once=True)
    engine = Engine([rule], [])
    fake = FakeExecutor({"once": ExecutionResult(err=RuntimeError("boom"))})
    engine._executor = fake
    await engine.dispatch(Event.STOP, {})
    engine._executor = FakeExecutor()
    await engine.dispatch(Event.STOP, {})
    assert [call[0] for call in engine._executor.calls] == ["once"]


async def test_async_rule_runs_in_background(capsys):
    engine = Engine([_rule("bg", Event.POST_TOOL_USE, async_mode=True)], [])
    fake = FakeExecutor()
    engine._executor = fake
    result = await engine.dispatch(Event.POST_TOOL_USE, {})
    assert result.injected_prompts == []
    assert fake.calls == []
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert [call[0] for call in fake.calls] == ["bg"]
    assert fake.calls[0][2] is False
    await engine.aclose()
    assert fake.closed is True


async def test_async_failure_is_logged_and_releases_once(capsys):
    engine = Engine([_rule("bg", Event.STOP, only_once=True, async_mode=True)], [])
    engine._executor = FakeExecutor({"bg": ExecutionResult(err=RuntimeError("nope"))})
    await engine.dispatch(Event.STOP, {})
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert "[hook bg] Stop failed: nope" in capsys.readouterr().err

    engine._executor = FakeExecutor()
    await engine.dispatch(Event.STOP, {})
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert [call[0] for call in engine._executor.calls] == ["bg"]


async def test_executor_exception_is_non_fatal(capsys):
    class RaisingExecutor:
        async def run(self, rule, payload, *, blocking):
            raise RuntimeError("kaboom")

        async def aclose(self):
            pass

    engine = Engine([_rule("broken", Event.STOP), _rule("good", Event.STOP)], [])
    engine._executor = RaisingExecutor()
    result = await engine.dispatch(Event.STOP, {})
    assert result.blocked is False
    assert "[hook broken] Stop failed: kaboom" in capsys.readouterr().err


async def test_sync_failure_logged_and_rule_continues(capsys):
    engine = Engine(
        [_rule("bad", Event.STOP), _rule("good", Event.STOP)],
        [],
    )
    engine._executor = FakeExecutor({"bad": ExecutionResult(err=RuntimeError("boom"))})
    result = await engine.dispatch(Event.STOP, {})
    assert result.blocked is False
    assert [call[0] for call in engine._executor.calls] == ["bad", "good"]
    assert "[hook bad] Stop failed: boom" in capsys.readouterr().err
