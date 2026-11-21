"""临时调研脚本：GitHub REST API 搜索候选仓库（调研结束后删除）。

用法: python scripts/_gh_probe.py qwen2.5-vl minicpm-v ...
结果追加写入 gh.json。
"""

import json
import os
import sys
import urllib.parse
import urllib.request


def search(q: str) -> list[dict]:
    url = (
        "https://api.github.com/search/repositories?q="
        + urllib.parse.quote(q)
        + "&sort=stars&per_page=6"
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "research", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("items", [])


path = "gh.json"
out = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}

for q in sys.argv[1:]:
    try:
        items = search(q)
    except Exception as exc:  # noqa: BLE001
        print("ERR", q, exc, flush=True)
        continue
    print("===", q, flush=True)
    rows = []
    for i in items:
        lic = (i.get("license") or {}).get("spdx_id")
        print(
            "%-44s %7d %-10s %s" % (i["full_name"], i["stargazers_count"], i.get("language"), lic),
            flush=True,
        )
        rows.append(
            {
                "full_name": i["full_name"],
                "stars": i["stargazers_count"],
                "lang": i.get("language"),
                "license": lic,
                "desc": i.get("description"),
                "url": i["html_url"],
                "pushed": i.get("pushed_at"),
                "homepage": i.get("homepage"),
            }
        )
    out[q] = rows

with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("saved", sum(len(v) for v in out.values()))
