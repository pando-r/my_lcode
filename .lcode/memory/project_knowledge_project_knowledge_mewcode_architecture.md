---
type: project_knowledge
title: lcode 项目核心架构
created: '2026-11-16T17:58:28.091869+08:00'
updated: '2026-11-16T17:58:28.091869+08:00'
---
lcode 是终端 AI 编程助手，项目路径 D:\Project\AgentProject\my_mecode。主链路：tui/app.py 的 submit() -> agent/__init__.py 的 Agent.run() ReAct 循环 -> llm/openai_provider.py 或 anthropic_provider.py 调用大模型 -> tool/*.py 执行工具 -> 结果回写会话继续循环。核心入口在 src/lcode/cli.py，工具注册在 tool/__init__.py 的 new_default_registry()。
