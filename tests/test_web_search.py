from __future__ import annotations

from mewcode.mcp_servers.web_search import (
    Hit,
    _clamp,
    _resolve_url,
    format_results,
    parse_results,
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
