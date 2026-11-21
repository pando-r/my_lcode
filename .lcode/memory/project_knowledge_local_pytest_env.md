---
type: project_knowledge
title: 当前解释器缺 pytest-asyncio 与 ruff
created: '2026-11-20T21:04:28.606982+08:00'
updated: '2026-11-20T21:04:28.606982+08:00'
---
这台机器默认的 python 是 C:\Users\29877\AppData\Local\Python\pythoncore-3.14-64，该环境里没装 pytest-asyncio 和 ruff。
后果：跑 tests/test_notify.py 时 4 个 async 用例（test_notify_tool_*）报「async def functions are not natively supported」而失败，另有 asyncio_mode / asyncio_default_fixture_loop_scope 未知配置项警告——这是环境问题，不是代码问题，pip install pytest-asyncio 即可。
ruff 检查同理跑不了（No module named ruff），格式/规范校验需另找可用的解释器或先安装。
