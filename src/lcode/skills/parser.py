"""Parse single-file or directory SKILL.md definitions."""

# 解析器
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from lcode.skills.types import SkillMeta, SkillSource

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")
VALID_MODES = ("inline", "fork")
VALID_CONTEXTS = ("full", "recent", "none")


class SkillParseError(Exception):
    # Raised when a SKILL.md cannot be parsed or fails validation.
    pass


@dataclass
class SkillDef:
    # 一个可执行的 Skill：frontmatter 元信息 + SOP 正文 + 来源。
    name: str
    description: str
    prompt_body: str
    mode: str = "inline"
    model: str | None = None
    context: str = "full"
    source_path: str = ""
    is_directory: bool = False
    source: SkillSource = SkillSource.USER
    source_dir: str = ""

    @property
    def is_fork(self) -> bool:
        return self.mode == "fork"

    @property
    def meta(self) -> SkillMeta:
        # prompt 层只需要只读元信息；mode/context 已在解析时校验。
        return SkillMeta(
            name=self.name,
            description=self.description,
            mode=self.mode,  # type: ignore[arg-type]
            context=self.context,  # type: ignore[arg-type]
            model=self.model,
        )


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    # 拆出 --- ... --- 之间的 YAML 与正文；缺失/未闭合/非映射都抛 SkillParseError。
    text = raw.lstrip("﻿")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SkillParseError("缺少起始 ---")

    end: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            end = index
            break
    if end is None:
        raise SkillParseError("frontmatter 未闭合")

    try:
        meta = yaml.safe_load("".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise SkillParseError(f"YAML 解析失败: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillParseError("frontmatter 必须是映射")

    body = "".join(lines[end + 1 :]).lstrip("\r\n")
    return meta, body


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _validate_meta(meta: dict) -> SkillMeta:
    # name 必须匹配 ^[a-z][a-z0-9-]*$；description 必填；未知 mode/context 降级并告警。
    name = _text(meta.get("name"))
    if not name:
        raise SkillParseError("缺少 name")
    if NAME_RE.match(name) is None:
        raise SkillParseError(f"非法 name: {name!r}")

    description = _text(meta.get("description"))
    if not description:
        raise SkillParseError("缺少 description")

    mode = _text(meta.get("mode")) or "inline"
    if mode not in VALID_MODES:
        logger.warning("未知 skill mode %r，按 inline 处理", mode)
        mode = "inline"

    context = _text(meta.get("context")) or "full"
    if context not in VALID_CONTEXTS:
        logger.warning("未知 skill context %r，按 full 处理", context)
        context = "full"

    model = _text(meta.get("model")) or None
    return SkillMeta(
        name=name,
        description=description,
        mode=mode,  # type: ignore[arg-type]
        context=context,  # type: ignore[arg-type]
        model=model,
    )


def parse_skill_file(
    path: str | Path,
    source: SkillSource = SkillSource.USER,
    is_directory: bool = False,
) -> SkillDef:
    # 读取并规范化一个 SKILL.md；IO/YAML 错误统一交给调用方决定是否跳过。
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    meta_raw, body = parse_frontmatter(raw)
    meta = _validate_meta(meta_raw)
    return SkillDef(
        name=meta.name,
        description=meta.description,
        prompt_body=body,
        mode=meta.mode,
        model=meta.model,
        context=meta.context,
        source_path=str(p.resolve()),
        is_directory=is_directory,
        source=source,
        source_dir=str(p.parent.resolve()),
    )


def parse_skill_dir(dir_path: str | Path, source: SkillSource = SkillSource.USER) -> SkillDef:
    # 目录型 Skill：<dir>/SKILL.md。
    path = Path(dir_path) / "SKILL.md"
    if not path.is_file():
        raise SkillParseError(f"缺少 SKILL.md: {path}")
    return parse_skill_file(path, source=source, is_directory=True)


def substitute_arguments(prompt_body: str, args: str) -> str:
    # F5：简单替换所有 $ARGUMENTS；没有占位符时原样返回。
    return prompt_body.replace("$ARGUMENTS", args)


__all__ = [
    "NAME_RE",
    "VALID_CONTEXTS",
    "VALID_MODES",
    "SkillDef",
    "SkillParseError",
    "parse_frontmatter",
    "parse_skill_dir",
    "parse_skill_file",
    "substitute_arguments",
]
