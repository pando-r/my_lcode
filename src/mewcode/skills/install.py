"""Install a skill from skills.sh / GitHub via the GitHub Contents API."""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import httpx2 as httpx

from mewcode.skills.loader import USER_SKILLS_DIR
from mewcode.skills.parser import SkillParseError, parse_skill_dir
from mewcode.skills.types import SkillSource

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MiB
MAX_TOTAL_SIZE = 8 * 1024 * 1024  # 8 MiB
MAX_FILE_COUNT = 64
MAX_RECURSION_DEPTH = 4
DOWNLOAD_TIMEOUT = 60.0
GITHUB_API = "https://api.github.com"


class InstallError(Exception):
    # 安装失败（URL 不支持、超限、路径非法、内容无效等）。
    pass


@dataclass(frozen=True)
class SkillURL:
    owner: str
    repo: str
    ref: str
    path: str


def parse_skill_url(url: str) -> SkillURL:
    # 支持 skills.sh / github.com tree / raw.githubusercontent.com 三种格式。
    parsed = urlsplit(url.strip())
    host = parsed.netloc.lower()
    parts = [part for part in parsed.path.split("/") if part]

    if host in ("skills.sh", "www.skills.sh"):
        if len(parts) < 2:
            raise InstallError(f"skills.sh URL 缺少 owner/repo: {url}")
        return SkillURL(parts[0], parts[1], "main", "/".join(parts[2:]))

    if host in ("github.com", "www.github.com"):
        if len(parts) < 2:
            raise InstallError(f"GitHub URL 缺少 owner/repo: {url}")
        if len(parts) >= 4 and parts[2] == "tree":
            return SkillURL(parts[0], parts[1], parts[3], "/".join(parts[4:]))
        return SkillURL(parts[0], parts[1], "main", "/".join(parts[2:]))

    if host == "raw.githubusercontent.com":
        if len(parts) < 4:
            raise InstallError(f"raw URL 缺少 owner/repo/ref/path: {url}")
        path = "/".join(parts[3:])
        if path.endswith("/SKILL.md"):
            path = path[: -len("/SKILL.md")]
        elif path.endswith(".md") and "/" in path:
            path = path.rsplit("/", 1)[0]
        return SkillURL(parts[0], parts[1], parts[2], path)

    raise InstallError(f"不支持的 Skill URL: {url}")


async def install_from_url(
    source: str,
    install_root: str | Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    # 下载到 staging → 校验 SKILL.md → atomic rename 到 ~/.mewcode/skills/<name>。
    parsed = parse_skill_url(source)
    root = Path(install_root) if install_root is not None else Path(USER_SKILLS_DIR).expanduser()
    root.mkdir(parents=True, exist_ok=True)

    if client is not None:
        return await _install_with_client(parsed, root, client)
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as new_client:
        return await _install_with_client(parsed, root, new_client)


async def _install_with_client(
    parsed: SkillURL, install_root: Path, client: httpx.AsyncClient
) -> str:
    staging = Path(tempfile.mkdtemp(prefix=".skill-install-", dir=str(install_root)))
    moved = False
    try:
        count = [0]
        total = [0]
        await _download_directory(parsed, parsed.path, staging, 0, count, total, client)

        try:
            skill = parse_skill_dir(staging, source=SkillSource.USER)
        except (SkillParseError, OSError) as exc:
            raise InstallError(f"安装内容缺少有效的 SKILL.md: {exc}") from exc

        target = install_root / skill.name
        if target.exists():
            raise InstallError(f"Skill 已存在: {skill.name}")

        staging.replace(target)
        moved = True
        return skill.name
    finally:
        if not moved:
            shutil.rmtree(staging, ignore_errors=True)


async def _download_directory(
    parsed: SkillURL,
    path: str,
    dest: Path,
    depth: int,
    count: list[int],
    total: list[int],
    client: httpx.AsyncClient,
) -> None:
    if depth > MAX_RECURSION_DEPTH:
        raise InstallError(f"目录深度超过 {MAX_RECURSION_DEPTH}: {path}")

    entries = await _fetch_contents(parsed, path, client)
    base = parsed.path.strip("/")

    for entry in entries:
        remote = str(entry.get("path", "")).strip()
        relative = _relative_path(remote, base)
        if relative is None:
            raise InstallError(f"非法路径: {remote!r}")

        entry_type = entry.get("type")
        if entry_type == "dir":
            await _download_directory(
                parsed, remote, dest / relative, depth + 1, count, total, client
            )
        elif entry_type == "file":
            await _download_file_entry(entry, dest / relative, count, total, client)
        else:
            raise InstallError(f"不支持的文件类型: {entry_type!r} ({remote})")


async def _download_file_entry(
    entry: dict,
    target: Path,
    count: list[int],
    total: list[int],
    client: httpx.AsyncClient,
) -> None:
    size = int(entry.get("size") or 0)
    if size > MAX_FILE_SIZE:
        raise InstallError(f"单文件超过 {MAX_FILE_SIZE} 字节: {entry.get('path')}")

    count[0] += 1
    if count[0] > MAX_FILE_COUNT:
        raise InstallError(f"文件数超过 {MAX_FILE_COUNT}")

    url = entry.get("download_url")
    if not url:
        raise InstallError(f"缺少 download_url: {entry.get('path')}")
    data = await _download_file(str(url), client)
    if len(data) > MAX_FILE_SIZE:
        raise InstallError(f"单文件超过 {MAX_FILE_SIZE} 字节: {entry.get('path')}")

    total[0] += len(data)
    if total[0] > MAX_TOTAL_SIZE:
        raise InstallError(f"总大小超过 {MAX_TOTAL_SIZE} 字节")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _relative_path(remote: str, base: str) -> Path | None:
    # 只接受 base 下的相对路径；拒绝 ..、绝对路径与空路径。
    path = PurePosixPath(remote)
    if path.is_absolute():
        return None
    parts = path.parts
    if not parts or any(part in ("..", "") for part in parts):
        return None

    if base:
        base_parts = PurePosixPath(base).parts
        if parts[: len(base_parts)] != base_parts:
            return None
        parts = parts[len(base_parts) :]
    if not parts:
        return None
    return Path(*parts)


async def _fetch_contents(parsed: SkillURL, path: str, client: httpx.AsyncClient) -> list[dict]:
    # GitHub Contents API：目录返回 list，文件返回 dict（统一成 list）。
    encoded = quote(path.strip("/"), safe="/")
    url = f"{GITHUB_API}/repos/{parsed.owner}/{parsed.repo}/contents/{encoded}"
    try:
        response = await client.get(
            url,
            params={"ref": parsed.ref},
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "mewcode",
            },
        )
    except httpx.HTTPError as exc:
        raise InstallError(f"请求 GitHub 失败: {exc}") from exc

    if response.status_code == 404:
        raise InstallError(f"GitHub 路径不存在: {parsed.owner}/{parsed.repo}/{path}")
    if response.status_code >= 400:
        raise InstallError(f"GitHub 返回 {response.status_code}: {url}")

    data = response.json()
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise InstallError("GitHub Contents API 返回格式异常")


async def _download_file(url: str, client: httpx.AsyncClient) -> bytes:
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise InstallError(f"下载文件失败: {url}: {exc}") from exc
    return response.content


__all__ = [
    "GITHUB_API",
    "MAX_FILE_COUNT",
    "MAX_FILE_SIZE",
    "MAX_RECURSION_DEPTH",
    "MAX_TOTAL_SIZE",
    "InstallError",
    "SkillURL",
    "install_from_url",
    "parse_skill_url",
]
