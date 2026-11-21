from __future__ import annotations

import pytest

from lcode.mcp_servers.web_search import (
    TAVILY_ENV,
    Hit,
    _clamp,
    _resolve_url,
    format_results,
    parse_bing,
    parse_results,
    parse_tavily,
    search,
)

HTML_PAGE = """
<div class="result results_links">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Falpha&amp;rut=abc">
     Alpha &amp; Beta</a>
  <a class="result__snippet" href="https://example.com/alpha">First <b>snippet</b>
     with   whitespace</a>
</div>
<div class="result results_links">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fgamma">Gamma</a>
  <a class="result__snippet" href="https://example.com/gamma">Second snippet</a>
</div>
"""

LITE_PAGE = """
<tr>
  <td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fbeta&amp;rut=z"
         class='result-link'>Beta &lt;page&gt;</a></td>
</tr>
<tr>
  <td class='result-snippet'> Lite &nbsp; snippet text </td>
</tr>
"""

AD_PAGE = """
<div class="result result--ad">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/y.js?ad_domain=ads.example">Buy now</a>
</div>
"""

BING_PAGE = """
<ol id="b_results">
<li class="b_algo">
  <h2><a href="https://example.com/one">第一 &amp; 条</a></h2>
  <div class="b_caption"><p class="b_lineclamp2">摘要  文本一</p></div>
</li>
<li class="b_algo">
  <h2><a href="https://example.com/two">Second</a></h2>
</li>
<li class="b_algo">
  <h2><a href="https://example.com/one">重复项</a></h2>
  <p>这条应被去重</p>
</li>
</ol>
"""


def test_parse_html_results():
    hits = parse_results(HTML_PAGE)
    assert [h.title for h in hits] == ["Alpha & Beta", "Gamma"]
    assert [h.url for h in hits] == ["https://example.com/alpha", "https://example.com/gamma"]
    assert hits[0].snippet == "First snippet with whitespace"
    assert hits[1].snippet == "Second snippet"


def test_parse_lite_results():
    hits = parse_results(LITE_PAGE)
    assert hits == [Hit("Beta <page>", "https://example.org/beta", "Lite snippet text")]


def test_parse_skips_ads_and_keeps_dedup():
    assert parse_results(AD_PAGE) == []
    duplicated = HTML_PAGE + HTML_PAGE
    assert len(parse_results(duplicated)) == 2


def test_parse_garbage_returns_empty():
    assert parse_results("<html><body>no results here</body></html>") == []


def test_resolve_url_variants():
    assert _resolve_url("https://example.com/x") == "https://example.com/x"
    assert _resolve_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.example%2Fp") == (
        "https://a.example/p"
    )
    assert _resolve_url("//duckduckgo.com/y.js?ad_domain=ads.example") is None
    assert _resolve_url("/relative/path") is None


def test_clamp():
    assert _clamp(0) == 1
    assert _clamp(99) == 10
    assert _clamp(3) == 3


def test_format_results():
    text = format_results("q", [Hit("T", "https://a.example", "s")])
    assert text.splitlines()[0] == "「q」的搜索结果（1 条）："
    assert "   https://a.example" in text


def test_format_truncates_long_snippet():
    text = format_results("q", [Hit("T", "https://a.example", "x" * 400)])
    assert "…" in text and "x" * 301 not in text


async def test_empty_query_does_not_hit_network():
    assert (await search("   ")).startswith("搜索失败")


# ── Bing 解析 ────────────────────────────────────────────────────────────


def test_parse_bing_results():
    hits = parse_bing(BING_PAGE)
    # 第三条与第一条 URL 重复，应被去掉
    assert [h.url for h in hits] == ["https://example.com/one", "https://example.com/two"]
    assert hits[0].title == "第一 & 条"
    assert hits[0].snippet == "摘要 文本一"
    assert hits[1].snippet == ""


def test_parse_bing_garbage_returns_empty():
    assert parse_bing("<html><body>没有结果</body></html>") == []


def test_parse_bing_skips_js_links():
    page = """
    <li class="b_algo"><h2><a href="/ck/a?u=javascript:void(0)">坏链接</a></h2></li>
    """
    assert parse_bing(page) == []


# ── Tavily 解析 ──────────────────────────────────────────────────────────


def test_parse_tavily_payload():
    payload = {
        "query": "q",
        "results": [
            {"title": "T1", "url": "https://a.example/1", "content": "c1", "score": 0.9},
            {"title": "T2", "url": "https://a.example/2", "content": ""},
            {"title": "no url", "content": "x"},
            "not-a-dict",
        ],
    }
    hits = parse_tavily(payload)
    assert [h.title for h in hits] == ["T1", "T2"]
    assert hits[0].snippet == "c1"


def test_parse_tavily_degenerate_payloads():
    assert parse_tavily({}) == []
    assert parse_tavily({"results": "nope"}) == []
    assert parse_tavily(None) == []
    assert parse_tavily([1, 2]) == []


# ── 引擎链 ───────────────────────────────────────────────────────────────


async def test_search_prefers_tavily(monkeypatch):
    from lcode.mcp_servers import web_search as mod

    monkeypatch.setenv(TAVILY_ENV, "tvly-test")
    calls: list[str] = []

    async def fake_tavily(query: str, api_key: str):
        calls.append("tavily")
        assert api_key == "tvly-test"
        return [Hit("T", "https://a.example", "s")]

    monkeypatch.setattr(mod, "_search_tavily", fake_tavily)
    out = await search("q")
    assert "https://a.example" in out
    assert calls == ["tavily"]  # 拿到结果就不再走下家


async def test_search_falls_back_when_tavily_fails(monkeypatch):
    from lcode.mcp_servers import web_search as mod

    monkeypatch.setenv(TAVILY_ENV, "tvly-test")
    monkeypatch.setattr(mod, "_search_tavily", _raise_connect_timeout)

    async def fake_bing(client, query):
        return [Hit("B", "https://bing.example", "s")]

    monkeypatch.setattr(mod, "_search_bing", fake_bing)
    out = await search("q")
    assert "https://bing.example" in out


async def test_search_without_tavily_key_skips_it(monkeypatch):
    from lcode.mcp_servers import web_search as mod

    monkeypatch.delenv(TAVILY_ENV, raising=False)
    seen: list[str] = []

    async def fake_tavily(query: str, api_key: str):
        seen.append("tavily")
        return []

    async def fake_bing(client, query):
        seen.append("bing")
        return [Hit("B", "https://bing.example", "s")]

    monkeypatch.setattr(mod, "_search_tavily", fake_tavily)
    monkeypatch.setattr(mod, "_search_bing", fake_bing)
    await search("q")
    assert seen == ["bing"]


async def test_search_reports_every_engine_failure(monkeypatch):
    # 全挂时要把「网络不通」和「没有结果」区分开，否则很难排查
    from lcode.mcp_servers import web_search as mod

    monkeypatch.setenv(TAVILY_ENV, "k")
    monkeypatch.setattr(mod, "_search_tavily", _raise_connect_timeout)
    monkeypatch.setattr(mod, "_search_bing", _raise_connect_timeout)
    monkeypatch.setattr(mod, "_ddg", _raise_connect_timeout)

    out = await search("q")
    assert out.startswith("搜索失败（所有引擎都不可用）")
    assert "tavily" in out and "bing" in out and "duckduckgo" in out
    assert "ConnectTimeout" in out


async def test_search_reports_no_results_separately(monkeypatch):
    # 引擎都通、但确实没结果时，不该报成「不可用」
    from lcode.mcp_servers import web_search as mod

    monkeypatch.delenv(TAVILY_ENV, raising=False)

    async def empty_bing(client, query):
        return []

    async def empty_ddg(client, query):
        return []

    monkeypatch.setattr(mod, "_search_bing", empty_bing)
    monkeypatch.setattr(mod, "_ddg", empty_ddg)

    out = await search("q")
    assert "没有找到" in out
    assert "不可用" not in out


async def _raise_connect_timeout(*args, **kwargs):
    # 用和 httpcore 同名的异常：日志里能直接对上真实故障
    raise ConnectTimeout("timed out")


class ConnectTimeout(Exception):
    pass
