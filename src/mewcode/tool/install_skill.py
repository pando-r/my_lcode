"""InstallSkill tool: download a skill from skills.sh / GitHub."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from mewcode.skills.install import InstallError, install_from_url
from mewcode.skills.loader import SkillLoader
from mewcode.tool import Result


class InstallSkillTool:
    # 写盘 + 联网：普通工具，受权限模式约束。
    read_only: bool = False

    def __init__(
        self,
        loader: SkillLoader | None = None,
        on_installed: Callable[[], None] | None = None,
        install_root: str | Path | None = None,
    ) -> None:
        self._loader = loader
        self._on_installed = on_installed
        self._install_root = Path(install_root) if install_root is not None else None

    def configure(
        self,
        loader: SkillLoader,
        on_installed: Callable[[], None] | None = None,
        install_root: str | Path | None = None,
    ) -> None:
        self._loader = loader
        self._on_installed = on_installed
        if install_root is not None:
            self._install_root = Path(install_root)

    def set_on_installed(self, callback: Callable[[], None] | None) -> None:
        self._on_installed = callback

    def name(self) -> str:
        return "InstallSkill"

    def description(self) -> str:
        return (
            "从 skills.sh 或 GitHub 安装一个 Skill 到 ~/.mewcode/skills；"
            "支持 skills.sh、github.com tree、raw.githubusercontent.com 链接。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Skill 的 URL"},
            },
            "required": ["source"],
        }

    async def execute(self, args: str) -> Result:
        if self._loader is None:
            return Result(content="InstallSkill not properly initialized", is_error=True)

        try:
            data = json.loads(args or "{}")
        except json.JSONDecodeError as exc:
            return Result(content=f"参数解析失败: {exc}", is_error=True)

        source = str(data.get("source", "")).strip()
        if not source:
            return Result(content="缺少参数: source", is_error=True)

        install_root = self._install_root or self._loader.user_dir
        try:
            name = await install_from_url(source, install_root=install_root)
        except InstallError as exc:
            return Result(content=f"安装失败: {exc}", is_error=True)

        self._loader.reload()
        if self._on_installed is not None:
            try:
                self._on_installed()
            except Exception as exc:  # noqa: BLE001 - install succeeded; refresh is best-effort
                return Result(
                    content=f"Skill '{name}' 已安装，但刷新命令失败: {exc}", is_error=True
                )
        return Result(content=f"Skill '{name}' 已安装到 ~/.mewcode/skills/{name}")


__all__ = ["InstallSkillTool"]
