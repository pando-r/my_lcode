"""Anthropic protocol adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace

import anthropic

from mewcode.config import ProviderConfig
from mewcode.llm import ROLE_TOOL, Message, Request, StreamEvent, ToolCall, Usage


class AnthropicProvider:
    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self._name = cfg.name
        self._model = cfg.model
        self._thinking = cfg.thinking
        self._client = anthropic.AsyncAnthropic(
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
        )

    def with_model(self, model: str) -> AnthropicProvider:
        # Skill 的 model 字段：沿用同一连接配置，只替换模型名。
        return AnthropicProvider(replace(self._cfg, model=model))

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        system_blocks = self._build_system_blocks(req)  # 把 system prompt 拆成块
        messages = self._to_anthropic_messages(req.messages)  # 把内部消息转成 Anthropic 消息
        self._append_reminder(messages, req.reminder)  # 把临时提醒追加到最后一轮

        params: dict = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system_blocks,
            "messages": messages,
        }
        if req.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in req.tools
            ]
        if self._thinking and not self._has_tool_history(req.messages):
            params["thinking"] = {"type": "enabled", "budget_tokens": 2048}

        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield StreamEvent(text=delta.text)
                        # thinking_delta / input_json_delta dropped; SDK accumulates input JSON

                final_message = await stream.get_final_message()
                usage = final_message.usage
                yield StreamEvent(
                    usage=Usage(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                        cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    )
                )
                calls = self._extract_tool_calls(final_message)
                if calls:
                    yield StreamEvent(tool_calls=calls)
            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any provider error as an event
            yield StreamEvent(err=exc)

    def _has_tool_history(self, msgs: list[Message]) -> bool:
        # 判断历史里是否已有工具轮次，用于决定是否启用 thinking
        return any(m.tool_calls or m.tool_results for m in msgs)

    def _build_system_blocks(self, req: Request) -> list[dict]:
        blocks: list[dict] = []
        if req.system.stable:
            blocks.append(
                {
                    "type": "text",
                    "text": req.system.stable,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        if req.system.environment:
            blocks.append({"type": "text", "text": req.system.environment})
        return blocks

    def _append_reminder(self, messages: list[dict], reminder: str) -> None:
        if not reminder:
            return
        if not messages:
            messages.append({"role": "user", "content": [{"type": "text", "text": reminder}]})
            return
        last = messages[-1]
        if last["role"] == "user":
            content = last["content"]
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            content.append({"type": "text", "text": reminder})
            last["content"] = content
        else:
            messages.append({"role": "user", "content": [{"type": "text", "text": reminder}]})

    def _extract_tool_calls(self, final_message) -> list[ToolCall]:
        if getattr(final_message, "stop_reason", None) != "tool_use":
            return []
        calls = []
        for block in final_message.content:
            if getattr(block, "type", None) == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, input=json.dumps(block.input)))
        return calls

    def _to_anthropic_messages(self, msgs: list[Message]) -> list[dict]:
        result = []
        for m in msgs:
            if m.role == "user":
                result.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                if m.tool_calls:
                    blocks = []
                    if m.content:
                        blocks.append({"type": "text", "text": m.content})
                    blocks += [
                        {
                            "type": "tool_use",
                            "id": c.id,
                            "name": c.name,
                            "input": json.loads(c.input or "{}"),
                        }
                        for c in m.tool_calls
                    ]
                    result.append({"role": "assistant", "content": blocks})
                else:
                    result.append({"role": "assistant", "content": m.content})
            elif m.role == ROLE_TOOL:
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_call_id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in m.tool_results
                ]
                result.append({"role": "user", "content": blocks})
        return result
