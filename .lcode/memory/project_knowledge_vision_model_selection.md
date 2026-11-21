---
type: project_knowledge
title: lcode vision 模型选型与多图总大小闸门
created: '2026-11-21T16:24:32.058532+08:00'
updated: '2026-11-21T16:24:32.058532+08:00'
---
lcode 的 vision 模型（src/lcode/vision.py，配置 .lcode/config.yaml 的 vision 段）：

选型：
- `qwen3-vl-flash` 已在千问平台「部分主线模型下线通知」名单里（输入 0.15 / 输出 1.5 元每百万 token），控制台看不到它。文档与 VISION_SETUP_HINT 已统一改回 `qwen3-vl-plus`。
- 教训：以用户在控制台实际可见的型号为准，不要照抄搜索结果的二手表格。
- 视觉能力已并入主线模型（Qwen3.8-Flash 原生输入模态含 Image/Text/Video），不必死找名字带 VL 的型号；判据是模型详情页「输入模态」里有 Image。
- Qwen3.8-Flash 部署范围只有中国香港/全球，不在北京地域。

多图总大小闸门（本次新增）：
- MAX_IMAGE_BYTES 仍是单图 5MB，MAX_IMAGES 6；新增 MAX_TOTAL_BYTES = 10MB + check_total_size()。
- 原因：DashScope 域名请求体上限 16 MiB，base64 膨胀 4/3；6×5MB 原始 = 40MB base64，必然 400。10MB → 13.3MB base64，稳在限制内。
- 在 tool/view_image.py 的 execute 里发请求前就调用 check_total_size，错误信息告诉模型「减张数 / 裁剪 / 调小 max_side」，而不是把服务端原始 400 甩给它。
- 测试断言超限时 asker.calls == []（确认没有白发请求）。
