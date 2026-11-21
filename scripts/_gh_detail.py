"""临时调研脚本：按 full_name 拉取仓库详情 + README 摘要（调研结束后删除）。"""

import json
import os
import re
import urllib.request

REPOS = [
    "m87-labs/moondream",
    "OpenBMB/MiniCPM-V",
    "PaddlePaddle/PaddleOCR",
    "RapidAI/RapidOCR",
    "microsoft/markitdown",
    "SYSTRAN/faster-whisper",
    "QwenLM/Qwen2.5-VL",
    "QwenLM/Qwen3-Omni",
    "docling-project/docling",
]


def get(url: str, binary: bool = False):
    req = urllib.request.Request(
        url, headers={"User-Agent": "research", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return raw if binary else json.load(r)


details = {}
for name in REPOS:
    try:
        d = get(f"https://api.github.com/repos/{name}")
    except Exception as exc:  # noqa: BLE001
        print("ERR repo", name, exc, flush=True)
        details[name] = {"error": str(exc)}
        continue
    details[name] = {
        "full_name": d["full_name"],
        "stars": d["stargazers_count"],
        "lang": d.get("language"),
        "license": (d.get("license") or {}).get("spdx_id"),
        "desc": d.get("description"),
        "pushed": d.get("pushed_at"),
        "size_kb": d.get("size"),
        "topics": d.get("topics"),
        "homepage": d.get("homepage"),
        "archived": d.get("archived"),
    }
    print("%-28s %7d %-10s %s" % (name, d["stargazers_count"], d.get("language"), (d.get("license") or {}).get("spdx_id")), flush=True)

with open("gh_details.json", "w", encoding="utf-8") as f:
    json.dump(details, f, ensure_ascii=False, indent=1)

# README 关键词探测：只抓关键片段，用于核实「能力边界 / 安装方式 / 显存」
os.makedirs("_readmes", exist_ok=True)
for name, meta in details.items():
    if "error" in meta:
        continue
    got = False
    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{name}/{branch}/README.md"
        try:
            text = get(url, binary=True).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        got = True
        path = "_readmes/" + name.replace("/", "__") + ".md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("README", name, branch, len(text), flush=True)
        break
    if not got:
        print("README MISSING", name, flush=True)

# 关键参数抽取：pip 安装名 / 显存 / 模型体积
print("\n--- signals ---")
for fn in sorted(os.listdir("_readmes")):
    text = open("_readmes/" + fn, encoding="utf-8").read()
    hits = []
    for pat in (r"pip install [\w\-\[\]=\.]+", r"\b\d+(?:\.\d+)?\s?GB\b", r"\b\d+(?:\.\d+)?B\b",
                r"VRAM", r"A100|RTX ?\d0\d0|CPU only", r"ollama run [\w\-\.:]+"):
        for m in re.finditer(pat, text, re.I):
            s = m.group(0).strip()
            if s not in hits:
                hits.append(s)
    print(fn, "=>", "; ".join(hits[:18]), flush=True)
