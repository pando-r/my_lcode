---
type: project_knowledge
title: lcode MCP 工具注册时机与可失败编排
created: '2026-11-20T00:26:21.463448+08:00'
updated: '2026-11-20T00:26:21.463448+08:00'
---
lcode 的 MCP server 只在 cli.py 启动时由 mcp/manager.py 注册一次，会话中新写的 tool 当轮无法调用，必须重启进程才能生效。

设计要点：manager.py 对单个 server 连接失败只 warn、不阻断启动——某个 MCP server 有 bug 最坏结果是启动多一行警告，不会拖垮整个 lcode。这种「可失败」编排是项目里值得借鉴的模式。

相关：agent/__init__.py 的 ReAct 循环、permission/engine.py 的四层权限流水线（每次 bash 调用都会过一遍）。
