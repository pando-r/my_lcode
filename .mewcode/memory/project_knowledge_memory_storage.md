---
type: project_knowledge
title: mewcode 长期记忆存储布局
created: '2026-11-17T19:29:39.789837+08:00'
updated: '2026-11-17T19:29:39.789837+08:00'
---
记忆分两级目录，由 cli.py:47-50 的 memory.Manager 构造：项目级 <cwd>/.mewcode/memory/（当前为 D:\Project\AgentProject\my_mecode\.mewcode\memory，随启动目录变化），用户级 ~/.mewcode/memory/（C:\Users\29877\.mewcode\memory）。每级都有 MEMORY.md 索引 + 若干笔记文件，两份索引在启动时 load_index() 读出并注入 system prompt（agent/__init__.py:707）。写入路径：agent/__init__.py:721 _maybe_update_memory() 每 MEMORY_UPDATE_INTERVAL 轮或检测到记忆信号时异步调 LLM，产出 create/update/delete 动作交给 memory/store.py 的 apply() 落盘。笔记为带 YAML frontmatter（type/title/created/updated）的 md，文件名 {type}_{slug}.md；MEMORY.md 每行格式 "- [type] title — 正文首行(截断120字符)"。TUI 中 /memory 命令列出已加载文件。
