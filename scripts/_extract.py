"""临时调研脚本：从已下载 README 中抽取关键行（调研结束后删除）。"""

import re
import sys

PATTERN = re.compile(r"(pip install|GPU|CUDA|VRAM|OCR|image|PDF|audio|video|Whisper|model size|LLM)", re.I)

for fn in sys.argv[1:]:
    with open("_readmes/" + fn, encoding="utf-8") as f:
        text = f.read()
    print("#####", fn, len(text))
    for line in text.splitlines():
        line = line.strip()
        if line and len(line) < 260 and PATTERN.search(line):
            print("   ", line[:220])
