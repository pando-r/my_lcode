"""view_image: ask a vision model about one or more images."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from lcode import vision as vision_mod
from lcode.config import VisionConfig
from lcode.tool import Result, _truncate
from lcode.tool.ctx import resolve_path
from lcode.vision import (
    MAX_ANSWER_CHARS,
    MAX_IMAGES,
    ImagePayload,
    VisionError,
    check_total_size,
    load_image,
)

# 视觉请求比普通工具慢得多（上传 + 推理），默认 30s 一定不够。
DEFAULT_TIMEOUT = 180.0

# 没配 vision 时的统一安装指引（/image 命令与工具本身共用同一份文案）。
# 型号是耗材，接口才是不变量：换模型只改 model 一行，不用动代码。
VISION_SETUP_HINT = (
    "vision 未配置，无法看图。请在 .lcode/config.yaml 加一段：\n"
    "vision:\n"
    "  protocol: openai        # 或 anthropic\n"
    "  api_key: sk-xxx\n"
    "  model: qwen3-vl-plus    # 填你控制台里真实可见的视觉模型\n"
    "  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# asker 的签名：(配置, 图片列表, 问题, detail) -> 答案文本
Asker = Callable[[VisionConfig, list[ImagePayload], str, str], Awaitable[str]]


class ViewImageTool:
    read_only: bool = True
    timeout: float = DEFAULT_TIMEOUT

    def __init__(
        self,
        vision: VisionConfig | None = None,
        asker: Asker | None = None,
    ) -> None:
        self._vision = vision
        self._asker = asker or vision_mod.ask_vision

    def name(self) -> str:
        return "view_image"

    def description(self) -> str:
        return (
            "用视觉模型看懂图片并按问题回答（截图 / 报错 / UI / 图表 / 照片）。"
            "主模型看不到像素，read_file 也读不了图片——需要知道图里有什么时用它。"
            "一次可传多张做对比。问得越具体，答案越准；看不清细节时用 crop 放大局部。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_IMAGES,
                    "description": "图片路径列表，最多 6 张（多张用于对比）",
                },
                "question": {
                    "type": "string",
                    "description": "想从图里得到什么，例如「这个报错在哪一行、原文是什么」",
                },
                "detail": {
                    "type": "string",
                    "enum": ["low", "high"],
                    "description": "low 更省 token（只认大结构），high 用于读小字（默认）",
                },
                "crop": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "只截取一块区域再看：[x1, y1, x2, y2]（原图像素坐标）",
                },
                "max_side": {
                    "type": "integer",
                    "description": "长边最大像素，默认 1568；调小可进一步省 token",
                },
            },
            "required": ["paths", "question"],
        }

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)
        if not isinstance(data, dict):
            return Result(content="参数必须是对象", is_error=True)

        paths = data.get("paths")
        if isinstance(paths, str):  # 容错：模型偶尔传单张字符串
            paths = [paths]
        if not isinstance(paths, list) or not paths:
            return Result(content="缺少参数: paths（图片路径列表）", is_error=True)
        if len(paths) > MAX_IMAGES:
            return Result(content=f"一次最多 {MAX_IMAGES} 张图，收到 {len(paths)} 张", is_error=True)

        question = str(data.get("question") or "").strip()
        if not question:
            return Result(content="缺少参数: question（要问图里什么）", is_error=True)

        cfg = self._vision
        if cfg is None or not cfg.ready():
            return Result(content=VISION_SETUP_HINT, is_error=True)

        detail = str(data.get("detail") or cfg.detail).strip().lower()
        if detail not in ("low", "high"):
            detail = "high"
        crop = data.get("crop")
        if crop is not None and not (
            isinstance(crop, list) and len(crop) == 4 and all(isinstance(v, int) for v in crop)
        ):
            return Result(content="crop 必须是 4 个整数：[x1, y1, x2, y2]", is_error=True)

        max_side = data.get("max_side")
        max_side = max_side if isinstance(max_side, int) and max_side >= 256 else cfg.max_image_side

        payloads: list[ImagePayload] = []
        for raw_path in paths:
            if not isinstance(raw_path, str) or not raw_path.strip():
                return Result(content=f"路径无效: {raw_path!r}", is_error=True)
            try:
                payloads.append(load_image(resolve_path(raw_path), max_side, crop))
            except VisionError as exc:
                return Result(content=str(exc), is_error=True)
            except OSError as exc:
                return Result(content=f"读取图片失败: {raw_path}（{exc}）", is_error=True)

        # 提前拦一次：等发请求才 400 的话，模型拿到的是服务端原始报错，
        # 它不知道该怎么办；这里能明确说「减张数 / 裁剪 / 调小 max_side」。
        try:
            check_total_size(payloads)
        except VisionError as exc:
            return Result(content=str(exc), is_error=True)

        try:
            answer = await self._asker(cfg, payloads, question, detail)
        except VisionError as exc:
            return Result(content=str(exc), is_error=True)
        except Exception as exc:  # noqa: BLE001 - 任何失败都要变成结构化结果
            return Result(content=f"看图失败: {exc}", is_error=True)
        if not answer.strip():
            answer = "（视觉模型没有返回内容；可以换个更具体的问题重试）"
        return Result(content=_format(answer, payloads, cfg.model))


def _format(answer: str, payloads: list[ImagePayload], model: str) -> str:
    # 元信息放在末尾：主模型先读到答案，再看到「这张图花了多少钱」。
    meta = ["", "---"]
    meta.extend(p.summary() for p in payloads)
    for p in payloads:
        if p.note:
            meta.append(f"  {p.note}")
    meta.append(f"[vision: {model}]")
    body = _truncate(answer.strip(), 400, MAX_ANSWER_CHARS)
    return body + "\n".join(meta)


__all__ = ["DEFAULT_TIMEOUT", "VISION_SETUP_HINT", "ViewImageTool"]
