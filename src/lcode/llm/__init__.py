"""Protocol-agnostic LLM interface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from lcode.config import ProviderConfig

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


class PromptTooLongError(Exception):
    # 提示词过长错误
    """Provider reported the context window was exceeded."""


@dataclass
class Usage:
    # Token usage for a single request.
    # token统计
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write: int = 0  # tokens written into cache on first fill
    cache_read: int = 0  # tokens read from cache on a hit


@dataclass
class System:
    # System prompt split into stable (cacheable) and environment (non-cacheable) parts.
    # 系统提示词
    stable: str = ""
    environment: str = ""


@dataclass
class Request:
    # All inputs for one provider request.
    # 一次请求的全部输入
    messages: list[Message] = field(default_factory=list)
    tools: list[ToolDefinition] = field(default_factory=list)
    system: System = field(default_factory=System)
    reminder: str = ""  # tagged supplementary instruction, not persisted #临时补充指令


@dataclass
class ToolCall:
    # A tool call requested by the model (after streaming assembly).
    # 模型请求调用工具
    id: str
    name: str
    input: str  # assembled raw JSON argument string


@dataclass
class ToolResult:
    # A tool execution result to feed back to the model.
    # 工具执行结果
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class ToolDefinition:
    # A protocol-agnostic tool definition sent with a request.
    # 工具定义
    name: str
    description: str
    input_schema: dict[str, Any]


# 消息
@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)  # assistant turns only
    tool_results: list[ToolResult] = field(default_factory=list)  # tool turns only


# 流事件
@dataclass
class StreamEvent:
    # A streaming event: text / tool_calls / usage / done / err.

    text: str = ""
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None  # emitted once before done when a request completes
    done: bool = False
    err: Exception | None = None


class Provider(Protocol):
    # A protocol-agnostic streaming LLM provider.
    # 适配器基类
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    # 用了异步生成器
    def stream(self, req: Request) -> AsyncIterator[StreamEvent]: ...


def new_provider(cfg: ProviderConfig) -> Provider:
    # Construct the right adapter for a provider config.
    if cfg.protocol == "anthropic":
        from lcode.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    if cfg.protocol == "openai":
        from lcode.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)
    raise ValueError(f"未知协议: {cfg.protocol!r}")
