"""临时调研脚本：只走 raw.githubusercontent 抓 README（不吃 API 配额）。"""

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

os.makedirs("_readmes", exist_ok=True)
for name in REPOS:
    got = False
    for fn in ("README.md", "readme.md", "README.rst"):
        for branch in ("main", "master"):
            url = f"https://raw.githubusercontent.com/{name}/{branch}/{fn}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "research"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    text = r.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                continue
            with open("_readmes/" + name.replace("/", "__") + ".md", "w", encoding="utf-8") as f:
                f.write(text)
            print("OK", name, branch, fn, len(text), flush=True)
            got = True
            break
        if got:
            break
    if not got:
        print("MISSING", name, flush=True)

print("\n--- signals ---")
for fn in sorted(os.listdir("_readmes")):
    text = open("_readmes/" + fn, encoding="utf-8").read()
    hits = []
    for pat in (
        r"pip install [\w\-\[\]=\.]+",
        r"\b\d+(?:\.\d+)?\s?GB\b",
        r"VRAM",
        r"ollama run [\w\-\.:]+",
        r"transport=|OpenAI-compatible|openai api",
    ):
        for m in re.finditer(pat, text, re.I):
            s = m.group(0).strip()
            if s not in hits:
                hits.append(s)
    print(fn, "=>", "; ".join(hits[:16]), flush=True)
