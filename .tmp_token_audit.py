"""从 session JSONL 里统计常驻任务的真实 token 消耗。"""

import json
from pathlib import Path

root = Path(r"D:\Project\AgentProject\my_mecode")
sessions = root / ".lcode" / "sessions"

rows = []
for d in sorted(sessions.iterdir(), key=lambda p: p.stat().st_mtime):
    jsonl = d / "conversation.jsonl"
    if not jsonl.is_file():
        continue
    entries = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not entries:
        continue
    first_user = next((e.get("content") for e in entries if e.get("role") == "user"), "")
    if not first_user:
        continue
    # 只挑我们之前跑的常驻测试任务
    if not any(k in first_user for k in ("测试任务", "只回答", "回答一个字", "echo hello", "tmp_resident_probe")):
        continue
    tool_calls = sum(len(e.get("tool_calls") or []) for e in entries)
    msgs = len(entries)
    assistant_chars = sum(len(e.get("content") or "") for e in entries if e.get("role") == "assistant")
    rows.append((d.name, first_user[:40], msgs, tool_calls, assistant_chars))

print(f"{'session':<22} {'msgs':>4} {'tools':>5} {'out_chars':>9}  任务")
for name, prompt, msgs, tools, chars in rows:
    print(f"{name:<22} {msgs:>4} {tools:>5} {chars:>9}  {prompt}")

print("\n注：JSONL 不含 usage 字段，token 需要从 provider 侧统计。")
