---
type: project_knowledge
title: lcode Conversation.from_messages 语义
created: '2026-11-16T20:19:43.273145+08:00'
updated: '2026-11-16T20:19:43.273145+08:00'
---
lcode 中 Conversation.from_messages(msgs, on_append, on_replace) 是「静默装载」构造路径：先用 cls(...) 建空会话（挂好回调），再 conv._messages = copy.deepcopy(list(msgs)) 直接替换私有字段，绕过 _append，因此不会触发 on_append/on_replace 回调。deepcopy 是为了断开与调用方的共享（Message 内含可变 tool_calls 列表），避免后续 append 污染原数据。使用场景：agent_tool.py 中 Fork 子 Agent（复制父历史但绝不能写进父会话 JSONL）、/resume 从磁盘恢复历史（不能反手再写一遍）。对比 add_user() -> _append() 路径会触发持久化回调。
