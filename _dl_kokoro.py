"""下载 Kokoro 中文 TTS 模型到 ~/.lcode/voice/models/kokoro-multi-lang-v1_1/。"""

import sys
import urllib.request
from pathlib import Path

REPO = "csukuangfj/kokoro-multi-lang-v1_1"
BASE = f"https://hf-mirror.com/{REPO}/resolve/main"
DEST = Path.home() / ".lcode" / "voice" / "models" / "kokoro-multi-lang-v1_1"

FILES = [
    "model.onnx",
    "voices.bin",
    "tokens.txt",
    "lexicon-zh.txt",
    "lexicon-us-en.txt",
    "date-zh.fst",
    "number-zh.fst",
    "phone-zh.fst",
    "dict/jieba.dict.utf8",
    "dict/hmm_model.utf8",
    "dict/user.dict.utf8",
    "dict/idf.utf8",
    "dict/stop_words.utf8",
    "dict/pos_dict/char_state_tab.utf8",
    "dict/pos_dict/prob_emit.utf8",
    "dict/pos_dict/prob_start.utf8",
    "dict/pos_dict/prob_trans.utf8",
]


def fetch(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "lcode-voice/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                sys.stderr.write(f"\r  {path.name} {pct:3d}% {done / 1048576:.1f}/{total / 1048576:.1f}MB")
                sys.stderr.flush()
    if total:
        sys.stderr.write("\n")
    tmp.replace(path)


for name in FILES:
    path = DEST / name
    if path.is_file() and path.stat().st_size > 0:
        print(f"skip {name}", flush=True)
        continue
    print(f"get  {name}", flush=True)
    fetch(f"{BASE}/{name}", path)

print("DONE", DEST)
