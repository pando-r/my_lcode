from __future__ import annotations

import json

import pytest

from mewcode.hook.executor import Executor, _find_sh
from mewcode.hook.rule import (
    Action,
    ActionType,
    HttpAction,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)

needs_sh = pytest.mark.skipif(_find_sh() is None, reason="no POSIX shell available")


def _rule(action: Action, timeout_s: float = 5.0) -> Rule:
    return Rule(name="test", event=None, action=action, timeout_s=timeout_s)  # type: ignore[arg-type]


def _shell(command: str, timeout_s: float = 5.0) -> Rule:
    return _rule(Action(type=ActionType.SHELL, shell=ShellAction(command)), timeout_s)


async def test_prompt_action_returns_text():
    executor = Executor()
    try:
        rule = _rule(Action(type=ActionType.PROMPT, prompt=PromptAction("hello")))
        result = await executor.run(rule, {}, blocking=False)
        assert result.prompt == "hello"
        assert result.err is None
    finally:
        await executor.aclose()


async def test_subagent_logs_placeholder(capsys):
    executor = Executor()
    try:
        rule = _rule(
            Action(
                type=ActionType.SUBAGENT,
                subagent=SubagentAction(agent_name="reviewer", prompt="go"),
            )
        )
        result = await executor.run(rule, {}, blocking=False)
        assert result.err is None
        assert capsys.readouterr().err.strip() == (
            "[hook subagent] not yet implemented, skipped: reviewer"
        )
    finally:
        await executor.aclose()


@needs_sh
async def test_shell_gets_sorted_json_on_stdin(tmp_path):
    target = (tmp_path / "payload.json").as_posix()
    executor = Executor()
    try:
        rule = _shell(f"cat > '{target}'")
        payload = {"b": 1, "a": "中文"}
        result = await executor.run(rule, payload, blocking=False)
        assert result.err is None
        written = (tmp_path / "payload.json").read_text(encoding="utf-8")
        assert written == json.dumps(payload, sort_keys=True, ensure_ascii=False)
    finally:
        await executor.aclose()


@needs_sh
async def test_shell_exit_2_blocks_only_blocking_events():
    executor = Executor()
    try:
        rule = _shell("printf 'denied' >&2; exit 2")
        blocked = await executor.run(rule, {}, blocking=True)
        assert blocked.blocked is True
        assert blocked.reason == "denied"
        assert blocked.err is None

        failed = await executor.run(rule, {}, blocking=False)
        assert failed.blocked is False
        assert failed.err is not None
        assert "exit 2" in str(failed.err)
    finally:
        await executor.aclose()


@needs_sh
async def test_shell_nonzero_exit_is_error():
    executor = Executor()
    try:
        result = await executor.run(_shell("printf 'oops' >&2; exit 3"), {}, blocking=True)
        assert result.blocked is False
        assert result.err is not None
        assert "exit 3" in str(result.err)
        assert "oops" in str(result.err)
    finally:
        await executor.aclose()


@needs_sh
async def test_shell_timeout_is_error():
    executor = Executor()
    try:
        result = await executor.run(_shell("sleep 5", timeout_s=0.2), {}, blocking=False)
        assert result.err is not None
        assert isinstance(result.err, TimeoutError)
        assert "timed out" in str(result.err)
    finally:
        await executor.aclose()


class _Response:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def request(self, method, url, *, content, headers, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "content": content,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return self.response

    async def aclose(self) -> None:
        pass


def _http(url: str = "http://example.test/hook", **kwargs) -> Rule:
    return _rule(Action(type=ActionType.HTTP, http=HttpAction(url=url, **kwargs)))


async def test_http_default_body_is_sorted_json():
    executor = Executor()
    fake = _Client(_Response("{}"))
    executor._client = fake  # type: ignore[assignment]
    try:
        result = await executor.run(_http(), {"b": 2, "a": 1}, blocking=False)
        assert result.err is None
        call = fake.calls[0]
        assert call["method"] == "POST"
        assert call["content"] == '{"a": 1, "b": 2}'
        assert call["timeout"] == 5.0
    finally:
        await executor.aclose()


async def test_http_body_template_and_headers():
    executor = Executor()
    fake = _Client(_Response("ok"))
    executor._client = fake  # type: ignore[assignment]
    try:
        rule = _http(
            body="cmd={tool_name}",
            headers={"X-Token": "t"},
            method="put",
        )
        result = await executor.run(rule, {"tool_name": "Bash"}, blocking=False)
        assert result.err is None
        call = fake.calls[0]
        assert call["method"] == "put"  # loader 负责规范化为大写
        assert call["content"] == "cmd=Bash"
        assert call["headers"] == {"X-Token": "t"}
    finally:
        await executor.aclose()


async def test_http_template_missing_field_is_error():
    executor = Executor()
    executor._client = _Client(_Response("ok"))  # type: ignore[assignment]
    try:
        result = await executor.run(_http(body="{missing}"), {}, blocking=False)
        assert result.err is not None
        assert "body template failed" in str(result.err)
    finally:
        await executor.aclose()


async def test_http_status_error():
    executor = Executor()
    executor._client = _Client(_Response("nope", status_code=500))  # type: ignore[assignment]
    try:
        result = await executor.run(_http(), {}, blocking=False)
        assert result.err is not None
        assert "HTTP 500" in str(result.err)
    finally:
        await executor.aclose()


async def test_http_blocking_decision():
    executor = Executor()
    executor._client = _Client(  # type: ignore[assignment]
        _Response('{"decision": "block", "reason": "no writes"}')
    )
    try:
        blocked = await executor.run(_http(), {}, blocking=True)
        assert blocked.blocked is True
        assert blocked.reason == "no writes"

        ignored = await executor.run(_http(), {}, blocking=False)
        assert ignored.blocked is False
        assert ignored.err is None
    finally:
        await executor.aclose()


async def test_http_invalid_json_response_is_error():
    executor = Executor()
    executor._client = _Client(_Response("not-json"))  # type: ignore[assignment]
    try:
        result = await executor.run(_http(), {}, blocking=True)
        assert result.err is not None
        assert "invalid JSON" in str(result.err)
    finally:
        await executor.aclose()
