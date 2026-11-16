"""Three-layer MEWCODE.md loader with @include expansion (F1-F6)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 独占一行的 @include <path>；段落中间的 @include 不匹配（因为 ^...$ 锚定整行）。
_INCLUDE_RE = re.compile(r"^@include\s+(.+?)\s*$")

MAX_INCLUDE_DEPTH = 5
_BINARY_PROBE_BYTES = 512


@dataclass
class Loader:
    # 项目根目录。项目级指令文件与 .mewcode/MEWCODE.md 都在它下面。
    project_root: str
    # 用户主目录；缺省用 Path.home()。用户级文件是 ~/.mewcode/MEWCODE.md。
    user_home: str = ""
    max_depth: int = MAX_INCLUDE_DEPTH
    # 供测试/诊断观察最近的警告（非必需）。
    warnings: list[str] = field(default_factory=list)

    # 按优先级加载三层指令文件，返回拼接后的完整指令文本。
    # ① <root>/MEWCODE.md ② <root>/.mewcode/MEWCODE.md ③ ~/.mewcode/MEWCODE.md
    # 加载失败的层静默跳过，全部为空返回空字符串。
    def load(self) -> str:
        root = Path(self.project_root).expanduser().resolve()
        home = Path(self.user_home).expanduser().resolve() if self.user_home else Path.home()
        home = home.resolve()

        targets: list[tuple[Path, Path]] = [
            (root / "MEWCODE.md", root),
            (root / ".mewcode" / "MEWCODE.md", root),
            (home / ".mewcode" / "MEWCODE.md", home / ".mewcode"),
        ]

        parts: list[str] = []
        for path, boundary in targets:
            try:
                text = self._load_file(str(path), str(boundary), 1, set())
            except Exception:  # noqa: BLE001 - instruction loading must never block startup
                logger.exception("加载指令文件失败: %s", path)
                text = ""
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    # 加载单个文件并展开其中的 @include。
    # boundary 是路径逃逸检测的根边界；depth 从 1 开始；visited 是当前展开链的绝对路径集合。
    def _load_file(self, path: str, boundary: str, depth: int, visited: set[str]) -> str:
        try:
            abs_path = str(Path(path).expanduser().resolve())
        except OSError:
            return ""

        # 环路检测：同一条展开链上同一个绝对路径只加载一次。
        if abs_path in visited:
            return self._warn("检测到环路", path)
        visited.add(abs_path)

        # 路径逃逸检测：解析（含符号链接）后必须仍在根边界之内。
        if not self._within(abs_path, boundary):
            return self._warn("路径超出允许范围", path)

        p = Path(abs_path)
        if not p.is_file():
            # 找不到的文件静默跳过（F6）。
            return ""

        try:
            data = p.read_bytes()
        except OSError:
            logger.warning("读取指令文件失败: %s", p)
            return ""

        if b"\x00" in data[:_BINARY_PROBE_BYTES]:
            return self._warn("二进制文件", path)

        text = data.decode("utf-8", errors="replace")
        return self._expand(text, p, boundary, depth, visited)

    def _expand(
        self, text: str, current: Path, boundary: str, depth: int, visited: set[str]
    ) -> str:
        out: list[str] = []
        for line in text.splitlines():
            match = _INCLUDE_RE.match(line)
            if match is None:
                out.append(line)
                continue

            rel = match.group(1)
            if depth >= self.max_depth:
                out.append(self._warn("超过最大嵌套深度", rel))
                continue

            target = current.parent / rel
            try:
                target_str = str(target.expanduser().resolve())
            except OSError:
                out.append(self._warn("路径超出允许范围", rel))
                continue

            out.append(self._load_file(target_str, boundary, depth + 1, visited))
        return "\n".join(out)

    @staticmethod
    def _within(abs_path: str, boundary: str) -> bool:
        try:
            return Path(abs_path).is_relative_to(Path(boundary).resolve())
        except (OSError, ValueError):
            return False

    def _warn(self, kind: str, path: str) -> str:
        message = f"<!-- @include {kind}，已跳过: {path} -->"
        self.warnings.append(message)
        return message
