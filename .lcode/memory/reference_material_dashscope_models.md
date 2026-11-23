---
type: reference_material
title: K 的百炼（DashScope）账户在用哪些模型
created: '2026-11-23T13:25:00+08:00'
updated: '2026-11-23T13:25:00+08:00'
---
主模型不在百炼上——主对话是 DeepSeek（`deepseek-flash`，base_url api.deepseek.com）。
百炼只承担「副模型」两条线，共用一把 key（环境变量 `DASHSCOPE_API_KEY`，sk-ws-…，116 字符）：

| 用途 | 模型 | 端点 | 配置位置 |
|---|---|---|---|
| 眼睛（view_image） | `qwen3-vl-plus` | `https://dashscope.aliyuncs.com/compatible-mode/v1`（OpenAI 兼容） | `.lcode/config.yaml` 的 `vision` 段 |
| 耳朵（/audio、语音 P2） | `qwen3-asr-flash` | `https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`（原生，不是 /audio/transcriptions，那个路径 404） | `.lcode/config.yaml` 的 `audio` 段 |

`vision` 段另有 `max_image_side: 1568` / `detail: high`；
`audio` 段另有 `language: zh` / `itn: true`（数字规范化）。

## 账号状态（2026-11-23 实测）

- 11:45 首次调用 `qwen3-asr-flash` 报 `400 Arrearage`（account is in good standing）。
- 13:20 复测：`qwen3-asr-flash` 200（转出「小可小可，现在几点？」）、
  `qwen3-vl-plus` 200（答「红色」）、`qwen-flash` 200。
→ **欠费是临时状态，会自己消失**。遇到 Arrearage 先看余额，别改代码。

## 探测小抄

- `GET https://dashscope.aliyuncs.com/compatible-mode/v1/models` 能列账号可见模型（200）。
- venv 里 HTTP 库叫 **`httpx2`**（不是 `httpx`），`audio.py:343` 就是这么 import 的。
- 原生 multimodal 端点探活时，纯 text 输入会报 `InvalidParameter: asr task 不支持该输入`
  ——那是参数错误，不代表鉴权失败；要看鉴权/计费得用真音频。
- 视觉探活别用 1x1 图：会报 `height/width must be larger than 10`。
