"""Memory note types and LLM update actions (F27-F31/F39)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


# 笔记分类
class NoteType(StrEnum):
    USER_PREFERENCE = "user_preference"  # 用户偏好
    CORRECTION_FEEDBACK = "correction_feedback"  # 修正反馈
    PROJECT_KNOWLEDGE = "project_knowledge"  # 项目知识
    REFERENCE_MATERIAL = "reference_material"  # 参考资料


# 笔记
class Note:
    type: NoteType
    title: str
    slug: str
    content: str
    filename: str
    created: datetime
    updated: datetime


# 更新操作
@dataclass
class UpdateAction:
    # action ∈ {"create", "update", "delete"}；level ∈ {"project", "user"}。
    action: str
    level: str
    type: str = ""
    title: str = ""
    slug: str = ""
    content: str = ""
    filename: str = ""

    #  from_dict() 是一个防御式工厂方法，作用是“脏数据不进系统”
    @classmethod
    def from_dict(cls, data: dict) -> UpdateAction | None:
        action = str(data.get("action", "")).strip().lower()
        level = str(data.get("level", "")).strip().lower()
        if action not in ("create", "update", "delete"):
            return None
        if level not in ("project", "user"):
            return None
        return cls(
            action=action,
            level=level,
            type=str(data.get("type", "")).strip(),
            title=str(data.get("title", "")).strip(),
            slug=str(data.get("slug", "")).strip(),
            content=str(data.get("content", "")),
            filename=str(data.get("filename", "")).strip(),
        )
