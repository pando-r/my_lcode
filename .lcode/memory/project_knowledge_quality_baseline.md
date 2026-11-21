---
type: project_knowledge
title: lcode 质量基线（ruff/mypy/测试/CI 现状）
created: '2026-11-20T00:59:30.360083+08:00'
updated: '2026-11-21T16:24:32.061100+08:00'
---
lcode 质量现状（最近核查）：测试 814 passed / 2 skipped（约 84s），只能手动跑；仓库无 CI（无 .github 目录）；ruff 仅 1 个错误（conversation.py 导入排序，--fix 可修）；mypy 106 个错误 / 30 个文件。

跑全量测试的可行办法：`python -m pytest tests -q > .pytest_out.txt 2>&1` 后台启动，分次 sleep 等待再读输出——直接前台跑会撞 bash 工具 30s 超时。
