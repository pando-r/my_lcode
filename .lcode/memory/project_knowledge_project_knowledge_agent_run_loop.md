---
type: project_knowledge
title: lcode Agent.run ReAct 循环关键设计
created: '2026-11-17T19:44:36.945068+08:00'
updated: '2026-11-17T19:44:36.945068+08:00'
---
lcode/src/lcode/agent/__init__.py 的 Agent.run() 是主 ReAct 循环（AsyncIterator[Event]），几个关键设计：
1) 生成器返回值靠外部 list：_stream_once / _execute_batched 是异步生成器、不能 return 值，用 box.append((text, calls, usage, ok)) 回传；run() 里 box[0] 解包。
2) 唯一正常出口：模型不再请求工具（not calls）→ add_assistant / turn_count+1 / STOP hook / _maybe_update_memory / yield done。有工具调用则不 return，继续下一轮迭代，形成闭环。
3) _ensure_assistant_tail：每条提前 return 的分支都要补一条 assistant 消息，因为 LLM 协议要求 user/assistant 角色交替，否则历史非法。
4) 三层熔断：MAX_ITERATIONS=25、MAX_UNKNOWN_RUN=3（连续三轮全是未注册或被白名单禁用的工具）、cancel 事件。
5) reminder 与 environment 文本每轮重建（LoadSkill 上一轮激活的 SOP 本轮可见），但不写入对话历史，只在本次 Request 临时附带。
6) _execute_batched 按只读性分批：连续只读工具用 asyncio.gather 并发，写/执行类串行并可能弹审批。
7) Agent 不依赖 TUI，只产出 Event，由 tui/app.py 消费渲染。
