---
type: project_knowledge
title: lcode 质量基线（ruff/mypy/测试/CI 现状）
created: '2026-11-20T00:59:30.360083+08:00'
updated: '2026-11-21T22:43:41.359713+08:00'
---
lcode 质量现状（最近核查，工具都在项目 .venv 里）：

- 全量测试：`.venv\Scripts\python.exe -m pytest tests -q` → 976 passed / 3 skipped / 2 failed（约 75s）。两个失败是 `tests/test_media.py::test_view_image_uses_config_detail_and_max_side` 与 `test_view_image_crop_out_of_range`，原因是本机缺 Pillow，属既有失败，与业务改动无关（改代码前就在失败）。
- 仓库无 CI（无 .github 目录），测试只能手动跑。
- ruff / mypy 装在 `.venv\Scripts\`（默认 python 解释器里没有）：`.venv\Scripts\ruff.exe check <files>`、`.venv\Scripts\mypy.exe src/lcode`。
- 全量测试耗时超过 bash 工具 30s 超时，需要用 `start /b cmd /c ".venv\Scripts\python.exe -m pytest tests -q > .tmp_x.txt 2>&1"` 后台跑，再读文件末尾几行；跑完记得删临时文件。
