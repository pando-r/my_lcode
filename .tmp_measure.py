"""实测常驻模式每轮请求的固定开销（system prompt + 工具定义）。"""

import asyncio
import json
import os
import sys

os.environ.setdefault("LCODE_PYTHON", sys.executable)

from lcode import __version__
from lcode.agent import Agent
from lcode.agent.runtime import SessionRuntime
from lcode.config import load
from lcode.llm import new_provider
from lcode.permission import new_engine
from lcode.prompt import gather_environment
from lcode.resident.runner import RESIDENT_SYSTEM_SUFFIX, RunnerOptions, TaskRunner
from lcode.tool import new_default_registry

ROOT = r"D:\Project\AgentProject\my_mecode"


def approx_tokens(text: str) -> int:
    # 粗估：中英混排约 1 token ≈ 2.5 字符（中文更接近 1 字 1 token，英文 4 字符）。
    return int(len(text) / 2.5)


async def main() -> None:
    cfg = load(os.path.join(ROOT, ".lcode", "config.yaml"))
    provider = new_provider(cfg.providers[0])
    registry = new_default_registry()
    engine, _ = new_engine(ROOT)

    runner = TaskRunner(cfg, RunnerOptions(root=ROOT), provider_index=0)
    await runner.start()

    # 1. 工具定义（每轮都随请求发送）
    defs = runner._registry.definitions()
    defs_json = json.dumps(
        [{"name": d.name, "description": d.description, "parameters": d.parameters} for d in defs],
        ensure_ascii=False,
    )
    print(f"工具数量            : {len(defs)}")
    print(f"工具定义字符数      : {len(defs_json)}  → 约 {approx_tokens(defs_json)} tokens")

    # 2. 系统提示（含 memory 索引 + 常驻后缀）
    runtime = SessionRuntime(
        memory_manager=runner._memory,
        instruction_text=runner._instruction_text,
        memory_text=runner._memory.load_index() if runner._memory else "",
    )
    agent = Agent(provider, runner._registry, __version__, engine, runtime=runtime)
    agent.append_system_prompt(RESIDENT_SYSTEM_SUFFIX)
    sys_prompt = agent._system_prompt()
    print(f"系统提示字符数      : {len(sys_prompt)}  → 约 {approx_tokens(sys_prompt)} tokens")

    # 3. environment 块
    env_text = gather_environment(__version__, provider.model).render()
    print(f"environment 字符数  : {len(env_text)}  → 约 {approx_tokens(env_text)} tokens")

    fixed = approx_tokens(defs_json) + approx_tokens(sys_prompt) + approx_tokens(env_text)
    print(f"\n每轮固定开销        : 约 {fixed} tokens（不含对话历史）")

    # 4. 现场实跑一条只读任务，拿真实 usage
    print("\n--- 实跑一条只读任务，读取真实 usage ---")
    await runner.close()

    runner2 = TaskRunner(cfg, RunnerOptions(root=ROOT, max_turns=6), provider_index=0)
    await runner2.start()
    real = await _run_with_usage(runner2, "用一句话回答：tests 目录里有多少个 .py 文件？")
    print(f"真实 input  : {real['input']}")
    print(f"真实 output : {real['output']}")
    print(f"真实 cache_r: {real['cache_read']}")
    await runner2.close()


async def _run_with_usage(runner, prompt: str) -> dict:
    """复用 TaskRunner 的构造逻辑跑一轮，但捕获 usage 事件。"""
    from lcode.compact import new_session_context
    from lcode.conversation import Conversation

    ses_ctx = new_session_context(runner._opts.root)
    runtime = SessionRuntime(
        memory_manager=runner._memory,
        instruction_text=runner._instruction_text,
        memory_text=runner._memory.load_index(),
        session=ses_ctx,
        hook_engine=runner._hook_engine,
    )
    agent = Agent(
        runner._provider,
        runner._registry,
        __version__,
        runner._engine,
        runtime=runtime,
        hook_engine=runner._hook_engine,
        max_turns=runner._opts.max_turns,
        permission_mode=runner._opts.mode,
        allowed_tools=runner._allowed_tools,
        is_subagent=True,
        denied_text=__import__(
            "lcode.resident.runner", fromlist=["RESIDENT_DENIED_TEXT"]
        ).RESIDENT_DENIED_TEXT,
    )
    agent.append_system_prompt(RESIDENT_SYSTEM_SUFFIX)

    conv = Conversation()
    conv.add_user(prompt)
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    cancel = asyncio.Event()
    import time

    started = time.monotonic()
    async for ev in agent.run(conv, runner._opts.mode, cancel):
        if ev.usage is not None:
            total["input"] += ev.usage.input
            total["output"] += ev.usage.output
            total["cache_read"] += ev.usage.cache_read
            total["cache_write"] += ev.usage.cache_write
        if ev.done:
            break
    print(f"耗时: {time.monotonic() - started:.1f}s")
    return total


asyncio.run(main())
