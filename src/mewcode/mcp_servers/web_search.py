"""内置 MCP server：网页搜索（DuckDuckGo，无需 API key）。

mewcode 会把它当成普通的 stdio MCP server 拉起：

    python -m mewcode.mcp_servers.web_search

对外只暴露一个工具 ``search_web``，注册到主 Agent 后的完整工具名是
``mcp__web__search_web``（前缀由 config 里的 server 名决定）。

实现取舍：不起第三方依赖，直接解析 DuckDuckGo 的 HTML 结果页。先打 html 端点，
拿不到结果（被反爬拦截 / 页面改版）再退回 lite 端点；两个端点是两套 DOM，所以
解析器同时认 ``result__a``/``result__snippet`` 与 ``result-link``/``result-snippet``。
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20.0
MAX_RESULTS_LIMIT = 10
SNIPPET_LIMIT = 300

_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
_LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"

# 一个正则同时匹配「标题链接」和「摘要块」，保证按文档顺序配对。
#   html 端点: <a rel="nofollow" class="result__a" href="...">标题</a>
#              <a class="result__snippet" ...>摘要</a>
#   lite 端点: <a rel="nofollow" href="..." class='result-link'>标题</a>
#              <td class='result-snippet'>摘要</td>
_RESULT_BLOCK_RE = re.compile(
    r"(?P<title_tag><a\b[^>]*class=[\"'][^\"']*result(?:__a|-link)[^\"']*[\"'][^>]*>)"
    r"(?P<title>.*?)</a>"
    r"|(?P<snippet_tag><(?:a|td)\b[^>]*class=[\"'][^\"']*result(?:__snippet|-snippet)[^\"']*[\"'][^>]*>)"
    r"(?P<snippet>.*?)</(?:a|td)>",
    re.S | re.I,
)
_HREF_RE = re.compile(r"href\s*=\s*[\"']([^\"']+)[\"']", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Hit:
    title: str
    url: str
    snippet: str


def _clean(text: str) -> str:
    # 去标签 → 反转义实体（&amp; / &nbsp;）→ 折叠空白，终端里才不会串行。
    return " ".join(html_lib.unescape(_TAG_RE.sub("", text)).split())


def _resolve_url(href: str) -> str | None:
    """把 DuckDuckGo 的跳转链接还原成目标地址；广告/站内链接返回 None。"""
    href = html_lib.unescape(href).strip()
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    host = parsed.netloc.lower()
    if host.endswith("duckduckgo.com"):
        if not parsed.path.startswith("/l/"):
            return None  # y.js 广告跳转等
        target = parse_qs(parsed.query).get("uddg")  # parse_qs 已完成百分号解码
        if not target or not target[0]:
            return None
        return target[0]
    if parsed.scheme in ("http", "https") and host:
        return href
    return None


def parse_results(page: str) -> list[Hit]:
    """从 DuckDuckGo 结果页（html 或 lite）里抽取标题 / URL / 摘要，按 URL 去重。"""
    hits: list[Hit] = []
    seen: set[str] = set()
    current: tuple[str, str] | None = None  # (title, url)
    snippet = ""

    def flush() -> None:
        nonlocal current, snippet
        if current is not None and current[1] not in seen:
            seen.add(current[1])
            hits.append(Hit(title=current[0], url=current[1], snippet=snippet))
        current, snippet = None, ""

    for match in _RESULT_BLOCK_RE.finditer(page):
        if match.group("title_tag") is not None:
            flush()
            href = _HREF_RE.search(match.group("title_tag"))
            url = _resolve_url(href.group(1)) if href else None
            title = _clean(match.group("title"))
            current = (title, url) if url else None
        elif current is not None:
            snippet = _clean(match.group("snippet"))
    flush()
    return hits


async def _fetch(client: httpx2.AsyncClient, endpoint: str, query: str) -> str:
    resp = await client.get(endpoint, params={"q": query, "kl": "wt-wt"})
    resp.raise_for_status()
    return resp.text


def format_results(query: str, hits: list[Hit]) -> str:
    lines = [f"「{query}」的搜索结果（{len(hits)} 条）："]
    for index, hit in enumerate(hits, 1):
        lines.append(f"{index}. {hit.title or '(无标题)'}")
        lines.append(f"   {hit.url}")
        if hit.snippet:
            snippet = hit.snippet[:SNIPPET_LIMIT]
            lines.append(f"   {snippet}{'…' if len(hit.snippet) > SNIPPET_LIMIT else ''}")
    return "\n".join(lines)


def _clamp(value: int) -> int:
    try:
        return max(1, min(int(value), MAX_RESULTS_LIMIT))
    except (TypeError, ValueError):
        return 5


async def search(query: str, max_results: int = 5) -> str:
    """搜索并返回可直接展示给模型的纯文本结果。"""
    q = (query or "").strip()
    if not q:
        return "搜索失败：query 不能为空。"

    try:
        async with httpx2.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            hits = parse_results(await _fetch(client, _HTML_ENDPOINT, q))
            if not hits:
                hits = parse_results(await _fetch(client, _LITE_ENDPOINT, q))
    except Exception as exc:  # noqa: BLE001 - 网络问题不应该让工具崩掉
        return f"搜索失败：{type(exc).__name__}: {exc}"

    if not hits:
        return f"没有找到与「{q}」相关的结果，可以换个关键词或减少限定条件。"
    return format_results(q, hits[: _clamp(max_results)])


# stdio server 的 stderr 会直接冒到 mewcode 的终端里，压掉每条 HTTP 请求的 INFO 日志。
for _noisy in ("httpx2", "httpcore2", "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

server = MCPServer(
    name="mewcode-web-search",
    version="0.1.0",
    instructions="提供 search_web 工具，通过 DuckDuckGo 搜索公开网页。",
    log_level="WARNING",
)


@server.tool(
    name="search_web",
    title="网页搜索",
    description=(
        "用 DuckDuckGo 搜索公开网页，返回若干条结果的标题、URL 与摘要。"
        "适合查最新信息、技术文档、新闻和报错原因；"
        "想限定站点时可在 query 里用 site: 语法，例如 'mcp protocol site:modelcontextprotocol.io'。"
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
)
async def search_web(query: str, max_results: int = 5) -> str:
    """Search the web with DuckDuckGo and return title/url/snippet per result.

    Args:
        query: 搜索关键词，支持 site: 等 DuckDuckGo 语法。
        max_results: 返回条数，1-10，默认 5。
    """
    return await search(query, max_results)


if __name__ == "__main__":
    server.run(transport="stdio")
