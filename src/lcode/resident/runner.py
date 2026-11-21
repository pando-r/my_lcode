"""无头执行器：把一条任务交给 Agent，不经过 TUI。

安全默认（重要）：常驻模式**没有人工审批通道**，所以 ``Agent.run_to_completion``
遇到 ASK 会显式拒绝。默认权限模式是 ``DEFAULT``，意味着：

- READ 类工具（read_file / grep / search_web …）→ 放行
- WRITE 类（write_file / edit_file）→ 被拒
- EXEC 类（bash）→ 被拒

想让它真能改文件，必须显式传 ``--resident-mode acceptEdits`` 或
``bypassPermissions``。黑名单与路径沙箱在任何模式下都照旧生效。
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from lcode import __version__
from lcode.agent import Agent
from lcode.agent.runtime import SessionRuntime
from lcode.compact import new_session_context
from lcode.config import Config, effective_context_window
from lcode.conversation import Conversation
from lcode.llm import new_provider
from lcode.memory import Manager as MemoryManager
from lcode.permission import Mode, new_engine
from lcode.resident.safeio import warn
from lcode.session import Writer
from lcode.tool import new_default_registry

# 常驻模式默认不开放这些工具：Team 协作与嵌套 Agent 都需要人在场。
EXCLUDED_TOOLS = frozenset(
    {"Agent", "TeamCreate", "TeamDelete", "TaskCreate", "TaskUpdate", "SendMessage"}
)

DEFAULT_MAX_TURNS = 20
DEFAULT_TIMEOUT = 900.0
DEFAULT_MODE = "default"

# 常驻模式没有人坐在终端前，但共享的 Agent 代码会把「无审批通道」记成
# 「用户拒绝执行」。不说清楚的话，模型会以为有人在场否决了它，于是反复重试
# 或干脆编造结果。这里同时改文案和系统提示，把真实原因告诉它。
RESIDENT_DENIED_TEXT = (
    "未执行：常驻模式没有人在场审批，该工具被自动拒绝。"
    "这不是用户否决你——请不要重试同一个调用，"
    "改为在最终回答里说明还需要什么权限。"
)

RESIDENT_SYSTEM_SUFFIX = """## 常驻模式（无人值守）

你现在运行在常驻队列里，没有人在终端前实时审批工具调用。

- 需要审批的工具（写文件、执行命令）会被**自动拒绝**，拒绝理由显示为「用户拒绝执行」。
  这不是用户反对你，而是当时没有审批通道。
- 遇到这种情况不要反复重试同一个调用，也不要假装执行成功。
- 正确做法：只完成只读部分，然后在回答里明确说明「还需要哪些权限」以及
  「拿到权限后准备做什么」，让下一个人读到结果时能直接决定是否提升权限。
- 你写下的回答会被存档成一份报告，读者可能在你结束后很久才看到。
- 如果需要用户尽快知道某件事，用 `notify` 工具发通知（它不受审批限制）：
  标题一句话说清发生了什么，正文给出最关键的一行结论。不要用它替代完整回答。"""


@dataclass
class RunnerOptions:
    root: str
    mode: Mode = Mode.DEFAULT
    max_turns: int = DEFAULT_MAX_TURNS
    timeout: float = DEFAULT_TIMEOUT
    allow_nested_agents: bool = False
    extra_allowed: list[str] = field(default_factory=list)


class TaskRunner:
    # 一次构造、多次复用：provider / MCP 连接 / 权限引擎只在启动时建一次，
    # 每轮任务只新建 Conversation + SessionRuntime，保证任务之间不互相污染上下文。
    def __init__(self, cfg: Config, opts: RunnerOptions, provider_index: int = 0) -> None:
        self._cfg = cfg
        self._opts = opts
        self._provider_index = provider_index
        self._mcp_mgr = None
        self._hook_engine = None
        self._registry = new_default_registry(cfg.vision, cfg.audio)
        self._provider = None
        self._engine = None
        self._memory: MemoryManager | None = None
        self._instruction_text = ""
        self._allowed_tools: list[str] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        from lcode import instructions as instructions_pkg
        from lcode import mcp as mcp_client

        root = self._opts.root
        os.environ.setdefault("LCODE_PYTHON", sys.executable)

        if not self._cfg.providers:
            raise RuntimeError("配置里没有可用的 provider")
        self._provider_index = min(self._provider_index, len(self._cfg.providers) - 1)
        self._provider = new_provider(self._cfg.providers[self._provider_index])

        mcp_cfg = mcp_client.load_config(root)
        self._mcp_mgr = await mcp_client.new_manager(mcp_cfg, __version__)
        for tool in self._mcp_mgr.tools():
            if self._registry.get(tool.name()) is None:
                self._registry.register(tool)

        try:
            from lcode.hook import load as load_hooks

            self._hook_engine = load_hooks(root)
        except Exception as exc:  # noqa: BLE001 - hooks 永远不能阻断常驻启动
            warn(f"[resident] warn: hooks 加载失败: {exc}")

        self._engine, engine_err = new_engine(root)
        if engine_err is not None:
            warn(f"[resident] warn: 权限引擎降级: {engine_err}")

        self._instruction_text = instructions_pkg.Loader(root).load()
        self._memory = MemoryManager(
            project_dir=str(Path(root) / ".lcode" / "memory"),
            user_dir=str(Path.home() / ".lcode" / "memory"),
        )
        self._memory.set_provider(self._provider, self._provider.model)

        excluded = set() if self._opts.allow_nested_agents else set(EXCLUDED_TOOLS)
        allowed = [
            definition.name
            for definition in self._registry.definitions()
            if definition.name not in excluded
        ]
        allowed.extend(self._opts.extra_allowed)
        self._allowed_tools = sorted(set(allowed))

    async def close(self) -> None:
        if self._hook_engine is not None:
            await self._hook_engine.aclose()
        if self._mcp_mgr is not None:
            await self._mcp_mgr.close()

    # ── introspection ────────────────────────────────────────────────────

    @property
    def tool_names(self) -> list[str]:
        return list(self._allowed_tools or [])

    def describe(self) -> str:
        model = self._provider.model if self._provider is not None else "?"
        return f"model={model} mode={self._opts.mode} tools={len(self._allowed_tools or [])}"

    # ── run one task ─────────────────────────────────────────────────────

    async def run(self, prompt: str, mode: Mode | None = None) -> str:
        """执行一条任务，返回最终助手文本；超时/取消会抛出对应异常。

        ``mode`` 覆盖本次任务的权限模式：邮件来源的任务必须能以只读模式跑，
        而 ``TaskRunner`` 一次构造全局复用，所以覆盖点只能落在每次调用上。
        """
        if self._provider is None or self._engine is None:
            raise RuntimeError("TaskRunner 尚未 start()")

        root = self._opts.root
        ses_ctx = new_session_context(root)
        writer = Writer(ses_ctx.session_dir)
        writer.model = self._provider.model
        try:
            runtime = SessionRuntime(
                memory_manager=self._memory,
                instruction_text=self._instruction_text,
                memory_text=self._memory.load_index() if self._memory is not None else "",
                session=ses_ctx,
                context_window=effective_context_window(self._cfg.providers[self._provider_index]),
                hook_engine=self._hook_engine,
            )
            agent = Agent(
                self._provider,
                self._registry,
                __version__,
                self._engine,
                runtime=runtime,
                hook_engine=self._hook_engine,
                max_turns=self._opts.max_turns,
                permission_mode=mode or self._opts.mode,
                allowed_tools=self._allowed_tools,
                is_subagent=True,  # 复用子 Agent 语义：不给 TUI，也没有审批升级通道
                denied_text=RESIDENT_DENIED_TEXT,
            )
            # 让模型知道「无人审批」而不是「有人否决」，否则它会误判并重试。
            agent.append_system_prompt(RESIDENT_SYSTEM_SUFFIX)
            conv = Conversation(on_append=writer.on_append, on_replace=writer.on_replace)
            conv.add_user(prompt)
            async with asyncio.timeout(self._opts.timeout):
                return await agent.run_to_completion(conv, "")
        finally:
            writer.close()


__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_MODE",
    "DEFAULT_TIMEOUT",
    "EXCLUDED_TOOLS",
    "RESIDENT_DENIED_TEXT",
    "RESIDENT_SYSTEM_SUFFIX",
    "RunnerOptions",
    "TaskRunner",
]
