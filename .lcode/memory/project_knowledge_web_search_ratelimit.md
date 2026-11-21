---
type: project_knowledge
title: lcode web_search MCP server 易被 DuckDuckGo 限流
created: '2026-11-20T00:32:34.143800+08:00'
updated: '2026-11-20T00:32:34.143800+08:00'
---
lcode 自带的 web_search MCP server（lcode/mcp_servers/web_search.py）底层走 DuckDuckGo 类搜索，连续请求会被限流：第一轮能返回结果，后续轮次直接返回「没有找到与「…」相关的结果」，且限流期间换任何关键词都查不到，容易误判为「真的搜不到」。

- 症状：exit_code 为 0、stdout 为空或「没有找到…」、stderr 为空。
- 应对：加退避重试（空结果时 sleep 数十秒再试），或降低单次会话的查询条数；注意 bash 工具 30s 超时，批量 sleep 容易直接超时。
- 该 server 目前没有内置 429/空结果重试，是一个可改进点。
