"""Two-level skill discovery with hot reload."""

from __future__ import annotations

import logging
from pathlib import Path

from lcode.skills.parser import SkillDef, parse_skill_dir, parse_skill_file
from lcode.skills.types import SkillSource

logger = logging.getLogger(__name__)

PROJECT_SKILLS_DIR = ".lcode/skills"
USER_SKILLS_DIR = "~/.lcode/skills"


class SkillLoader:
    # 项目级优先于用户级：按 project → user 顺序扫描，首次出现的 name 占位。
    def __init__(self, work_dir: str | Path, user_dir: str | Path | None = None) -> None:
        self._work_dir = Path(work_dir)
        self._project_dir = self._work_dir / PROJECT_SKILLS_DIR
        self._user_dir = (
            Path(user_dir) if user_dir is not None else Path(USER_SKILLS_DIR).expanduser()
        )
        self._skills: dict[str, SkillDef] = {}
        self._cache: dict[str, SkillDef] = {}

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    @property
    def user_dir(self) -> Path:
        return self._user_dir

    def load_all(self) -> dict[str, SkillDef]:
        # 启动期全量扫描；单条解析失败只 warning，不阻断其它 skill。
        self._skills.clear()
        self._cache.clear()
        self._scan_directory(self._project_dir, SkillSource.PROJECT)
        self._scan_directory(self._user_dir, SkillSource.USER)
        return dict(self._skills)

    def _scan_directory(self, root: Path, source: SkillSource) -> None:
        if not root.is_dir():
            return
        try:
            entries = sorted(root.iterdir())
        except OSError as exc:
            logger.warning("读取 skill 目录失败 %s: %s", root, exc)
            return

        for entry in entries:
            try:
                if entry.is_dir():
                    skill_path = entry / "SKILL.md"
                    if not skill_path.is_file():
                        continue
                    skill = parse_skill_dir(entry, source=source)
                elif entry.is_file() and entry.suffix == ".md":
                    skill = parse_skill_file(entry, source=source, is_directory=False)
                else:
                    continue
            except Exception as exc:  # noqa: BLE001 - one bad skill must not abort the scan
                logger.warning("Skipping %s skill '%s': %s", source.value, entry.name, exc)
                continue

            if skill.name in self._skills:
                continue
            self._skills[skill.name] = skill
            self._cache[skill.name] = skill

    def get(self, name: str) -> SkillDef | None:
        # 每次调用都重读源文件实现热重载；失败回退缓存旧版本。
        cached = self._skills.get(name)
        if cached is None:
            return None
        try:
            if cached.is_directory:
                fresh = parse_skill_dir(cached.source_dir, source=cached.source)
            else:
                fresh = parse_skill_file(
                    cached.source_path, source=cached.source, is_directory=False
                )
        except Exception as exc:  # noqa: BLE001 - fall back to the cached copy
            logger.warning("重读 skill '%s' 失败: %s（回退缓存）", name, exc)
            return self._cache.get(name, cached)

        self._cache[name] = fresh
        self._skills[name] = fresh
        return fresh

    def reload(self) -> tuple[list[str], list[str]]:
        # 重新扫描两级目录，返回 (新增, 移除)。
        before = set(self._skills)
        self.load_all()
        after = set(self._skills)
        return sorted(after - before), sorted(before - after)

    def list(self) -> list[SkillDef]:
        return [self._skills[name] for name in sorted(self._skills)]

    def names(self) -> list[str]:
        return sorted(self._skills)

    def get_catalog(self) -> list[tuple[str, str]]:
        return [(skill.name, skill.description) for skill in self.list()]

    def get_source_label(self, name: str) -> str:
        # 按源路径前缀区分 project / user；未知 skill 返回 ""。
        skill = self._skills.get(name)
        if skill is None:
            return ""
        try:
            path = Path(skill.source_path).resolve()
            if path.is_relative_to(self._project_dir.resolve()):
                return SkillSource.PROJECT.value
            if path.is_relative_to(self._user_dir.resolve()):
                return SkillSource.USER.value
        except OSError:
            pass
        return skill.source.value


__all__ = ["PROJECT_SKILLS_DIR", "USER_SKILLS_DIR", "SkillLoader"]
