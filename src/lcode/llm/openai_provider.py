"""OpenAI protocol adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

import openai

from lcode.config import ProviderConfig
from lcode.llm import ROLE_TOOL, Message, Request, StreamEvent, ToolCall, Usage


class OpenAIProvider:
    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self._name = cfg.name
        self._model = cfg.model
        self._client = openai.AsyncOpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
        )

    def with_model(self, model: str) -> OpenAIProvider:
        # Skill 的 model 字段：沿用同一连接配置，只替换模型名。
        return OpenAIProvider(replace(self._cfg, model=model))

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        system_text = req.system.stable
        if req.system.environment:
            system_text = (
                system_text + "\n\n" + req.system.environment
                if system_text
                else req.system.environment
            )

        messages: list[dict] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages += self._to_openai_messages(req.messages)
        if req.reminder:
            messages.append({"role": "user", "content": req.reminder})

        params: dict = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if req.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in req.tools
            ]

        try:
            stream = await self._client.chat.completions.create(**params)
            tool_calls_buf: dict[int, dict[str, str]] = {}
            async for chunk in stream:
                if chunk.usage is not None:
                    details = getattr(chunk.usage, "prompt_tokens_details", None)
                    cached = getattr(details, "cached_tokens", 0) or 0
                    yield StreamEvent(
                        usage=Usage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                            cache_read=cached,
                        )
                    )
                    continue
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue
                delta = choice.delta
                if delta and delta.content:
                    yield StreamEvent(text=delta.content)
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        buf = tool_calls_buf.setdefault(
                            tc.index, {"id": "", "name": "", "args": ""}
                        )
                        if tc.id:
                            buf["id"] = tc.id
                        if tc.function and tc.function.name:
                            buf["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            buf["args"] += tc.function.arguments

            calls = []
            if tool_calls_buf:
                for idx in sorted(tool_calls_buf):
                    v = tool_calls_buf[idx]
                    calls.append(ToolCall(id=v["id"], name=v["name"], input=v.get("args") or "{}"))
            if calls:
                yield StreamEvent(tool_calls=calls)
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any provider error as an event
            yield StreamEvent(err=exc)

    def _to_openai_messages(self, msgs: list[Message]) -> list[dict]:
        result = []
        for m in msgs:
            if m.role == "user":
                result.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                if m.tool_calls:
                    result.append(
                        {
                            "role": "assistant",
                            "content": m.content or None,
                            "tool_calls": [
                                {
                                    "id": c.id,
                                    "type": "function",
                                    "function": {"name": c.name, "arguments": c.input or "{}"},
                                }
                                for c in m.tool_calls
                            ],
                        }
                    )
                else:
                    result.append({"role": "assistant", "content": m.content})
            elif m.role == ROLE_TOOL:
                for r in m.tool_results:
                    result.append(
                        {"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content}
                    )
        return result
