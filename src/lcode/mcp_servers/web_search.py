"""内置 MCP server：网页搜索（多引擎回退，无需额外进程）。

lcode 会把它当成普通的 stdio MCP server 拉起：

    python -m lcode.mcp_servers.web_search

对外只暴露一个工具 ``search_web``，注册到主 Agent 后的完整工具名是
``mcp__web__search_web``（前缀由 config 里的 server 名决定）。

引擎链（按顺序试，第一个拿到结果就停）：

1. **Tavily**（需要环境变量 ``TAVILY_API_KEY``）——API 型，结果质量最好，
   不受页面改版影响。没配就跳过。
2. **Bing HTML**——国内大多数网络环境下可直连。DuckDuckGo 常被 DNS 污染，
   而 DDG 被污染时 TCP 会一直超时，所以 Bing 必须排在 DDG 前面，
   否则每次搜索都要先白等两个超时。
3. **DuckDuckGo html / lite**——保留给 Bing 被墙、DDG 可用的网络环境。

每个引擎有独立超时：链路里一个死引擎不该吃掉整轮搜索的时间预算。
"""

from __future__ import annotations

import html as html_lib
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_RESULTS_LIMIT = 10
SNIPPET_LIMIT = 300

# 各引擎超时。DDG 在被污染的网络上表现为「连不上」，超时要短，
# 否则两个端点加起来会让一次搜索卡多半分钟。
TAVILY_TIMEOUT = 30.0
BING_TIMEOUT = 15.0
DDG_TIMEOUT = 8.0

TAVILY_ENV = "TAVILY_API_KEY"
_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_BING_ENDPOINT = "https://www.bing.com/search"
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

# Bing 把每条结果包在 <li class="b_algo"> 里，标题在 <h2><a href>，摘要常是 <p>。
_BING_BLOCK_RE = re.compile(r'<li class="b_algo".*?</li>', re.S)
_BING_TITLE_RE = re.compile(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S)
_BING_SNIPPET_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S)

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


def parse_bing(page: str) -> list[Hit]:
    """从 Bing 结果页里抽取标题 / URL / 摘要，按 URL 去重。"""
    hits: list[Hit] = []
    seen: set[str] = set()
    for block in _BING_BLOCK_RE.findall(page):
        match = _BING_TITLE_RE.search(block)
        if not match:
            continue
        url = _resolve_url(match.group(1))
        if not url or url in seen:
            continue
        snippet_match = _BING_SNIPPET_RE.search(block)
        snippet = _clean(snippet_match.group(1)) if snippet_match else ""
        seen.add(url)
        hits.append(Hit(title=_clean(match.group(2)), url=url, snippet=snippet))
    return hits


def parse_tavily(payload: object) -> list[Hit]:
    """把 Tavily 的 JSON 响应转成统一的 Hit 列表。"""
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    hits: list[Hit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        hits.append(
            Hit(
                title=str(item.get("title") or "").strip(),
                url=url,
                snippet=str(item.get("content") or "").strip(),
            )
        )
    return hits


# ── 引擎实现 ─────────────────────────────────────────────────────────────


async def _search_tavily(query: str, api_key: str) -> list[Hit]:
    async with httpx2.AsyncClient(timeout=TAVILY_TIMEOUT) as client:
        resp = await client.post(
            _TAVILY_ENDPOINT,
            json={"query": query, "max_results": MAX_RESULTS_LIMIT, "search_depth": "basic"},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return parse_tavily(resp.json())


async def _search_bing(client: httpx2.AsyncClient, query: str) -> list[Hit]:
    resp = await client.get(
        _BING_ENDPOINT,
        params={"q": query, "setlang": "zh-CN"},
        timeout=BING_TIMEOUT,
    )
    resp.raise_for_status()
    return parse_bing(resp.text)


async def _fetch(client: httpx2.AsyncClient, endpoint: str, query: str) -> str:
    resp = await client.get(endpoint, params={"q": query, "kl": "wt-wt"}, timeout=DDG_TIMEOUT)
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


async def _ddg(client: httpx2.AsyncClient, query: str) -> list[Hit]:
    hits = parse_results(await _fetch(client, _HTML_ENDPOINT, query))
    if not hits:
        hits = parse_results(await _fetch(client, _LITE_ENDPOINT, query))
    return hits


async def search(query: str, max_results: int = 5) -> str:
    """搜索并返回可直接展示给模型的纯文本结果。"""
    q = (query or "").strip()
    if not q:
        return "搜索失败：query 不能为空。"

    engines: list[tuple[str, Callable[[], Awaitable[list[Hit]]]]] = []
    api_key = os.environ.get(TAVILY_ENV, "").strip()
    if api_key:
        engines.append(("tavily", lambda: _search_tavily(q, api_key)))

    client = httpx2.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        follow_redirects=True,
    )
    engines.append(("bing", lambda: _search_bing(client, q)))
    engines.append(("duckduckgo", lambda: _ddg(client, q)))

    failures: list[str] = []
    try:
        for name, run in engines:
            try:
                hits = await run()
            except Exception as exc:  # noqa: BLE001 - 单个引擎故障要能降级到下一个
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if hits:
                return format_results(q, hits[: _clamp(max_results)])
    finally:
        await client.aclose()

    if failures:
        # 全挂时把原因带出来：分不清「网络不通」和「没有结果」会浪费很多轮调试。
        return "搜索失败（所有引擎都不可用）：\n" + "\n".join(f"- {line}" for line in failures)
    return f"没有找到与「{q}」相关的结果，可以换个关键词或减少限定条件。"


# stdio server 的 stderr 会直接冒到 lcode 的终端里，压掉每条 HTTP 请求的 INFO 日志。
for _noisy in ("httpx2", "httpcore2", "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

server = MCPServer(
    name="lcode-web-search",
    version="0.2.0",
    instructions="提供 search_web 工具，通过 Tavily / Bing / DuckDuckGo 搜索公开网页。",
    log_level="WARNING",
)


@server.tool(
    name="search_web",
    title="网页搜索",
    description=(
        "搜索公开网页，返回若干条结果的标题、URL 与摘要。"
        "适合查最新信息、技术文档、新闻和报错原因；"
        "想限定站点时可在 query 里用 site: 语法，例如 'mcp protocol site:modelcontextprotocol.io'。"
    ),
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
)
async def search_web(query: str, max_results: int = 5) -> str:
    """Search the web and return title/url/snippet per result.

    Args:
        query: 搜索关键词，支持 site: 等检索语法。
        max_results: 返回条数，1-10，默认 5。
    """
    return await search(query, max_results)


if __name__ == "__main__":
    server.run(transport="stdio")
