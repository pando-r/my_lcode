---
type: project_knowledge
title: lcode MCP server 配置与工具命名规则
created: '2026-11-20T00:32:34.143134+08:00'
updated: '2026-11-20T00:32:34.143134+08:00'
---
lcode 的 MCP server 在 .lcode.yaml 的 mcp_servers 段配置：

```yaml
mcp_servers:
  context7:
    type: stdio
    command: "npx"
    args: ["-y", "@upstash/context7-mcp"]
```

- 改完必须重启进程才生效（注册只在 cli.py 启动时发生一次）。
- 注册后工具名带前缀 `mcp__<server>__<tool>`，例如 `mcp__context7__*`、`mcp__playwright__*`。
- 因为带 `mcp__` 前缀，这些工具会自动进入后台子 Agent 的工具白名单——判定逻辑在 tool/filter.py 的 `is_mcp_or_skill()` 里，和 Skill 共用同一条路径。
