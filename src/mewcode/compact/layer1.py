"""Layer-1 preventive compression: offload oversized tool results to disk."""

# 唯一已完全实现的核心策略：当单个工具结果过大或一条消息内多个工具结果合计过大时，
# 落盘并在消息中替换为头部预览
from __future__ import annotations

import copy
from pathlib import Path

from mewcode.compact.const import (
    MESSAGE_AGGREGATE_LIMIT,
    PREVIEW_HEAD_BYTES,
    PREVIEW_HEAD_LINES,
    SINGLE_RESULT_LIMIT,
)
from mewcode.compact.state import ContentReplacementState, SessionContext
from mewcode.llm import Message, ToolResult


def spill_single(session: SessionContext, tool_use_id: str, content: str) -> None:
    """Write a single tool result to disk, idempotently. Raises OSError on failure."""
    """将单个工具结果落盘"""
    path = Path(session.spill_dir) / tool_use_id
    if path.exists():
        return
    path.write_bytes(content.encode("utf-8"))


def _head_preview(content: str) -> str:
    """snip to PREVIEW_HEAD_BYTES, but no more than PREVIEW_HEAD_LINES lines."""
    """提取文本的头部预览"""
    head = "".join(content.splitlines(keepends=True)[:PREVIEW_HEAD_LINES])
    data = head.encode("utf-8")
    if len(data) > PREVIEW_HEAD_BYTES:
        head = data[:PREVIEW_HEAD_BYTES].decode("utf-8", errors="ignore")
    return head


def build_preview(original_bytes: int, head: str, spill_path: str) -> str:
    return "\n".join(
        [
            f"[content offloaded] original size: {original_bytes} bytes",
            f"[saved to] {spill_path}",
            "[head preview]",
            head,
            "完整内容已保存到上述路径，如需查看请用文件读取工具读取该路径，不要凭头部预览猜测全文",
        ]
    )


def offload_and_snip(
    msgs: list[Message],
    state: ContentReplacementState,
    session: SessionContext,
) -> list[Message]:
    out = []
    for m in msgs:
        if m.role == "tool":
            out.append(Message(role="tool", tool_results=_process(m.tool_results, state, session)))
        else:
            out.append(copy.deepcopy(m))
    return out


def _process(
    results: list[ToolResult],
    state: ContentReplacementState,
    session: SessionContext,
) -> list[ToolResult]:
    """处理工具结果列表"""
    n = len(results)
    new_contents: list[str | None] = [None] * n

    # already-decided items are frozen: reuse the ledger result
    undecided: list[int] = []
    for i in range(n):
        cid = results[i].tool_call_id
        if cid in state._seen_ids:
            new_contents[i] = state.decide_once(cid, results[i].content, lambda: ("kept", ""))
        else:
            undecided.append(i)

    undecided.sort(key=lambda i: len(results[i].content.encode("utf-8")), reverse=True)

    # Phase 1: single-item threshold
    spilled: set[int] = set()
    for i in undecided:
        if len(results[i].content.encode("utf-8")) > SINGLE_RESULT_LIMIT:
            if _try_spill(results[i], new_contents, i, state, session):
                spilled.add(i)

    # Phase 2: aggregate threshold
    remaining = [i for i in undecided if i not in spilled]
    agg = sum(len(results[i].content.encode("utf-8")) for i in remaining)
    for i in remaining:
        if agg <= MESSAGE_AGGREGATE_LIMIT:
            break
        if _try_spill(results[i], new_contents, i, state, session):
            agg -= len(results[i].content.encode("utf-8"))

    # Kept items (undecided, not spilled) are frozen as kept.
    for i in undecided:
        if new_contents[i] is None:
            new_contents[i] = state.decide_once(
                results[i].tool_call_id, results[i].content, lambda: ("kept", "")
            )

    final = []
    for i in range(n):
        content = new_contents[i] if new_contents[i] is not None else results[i].content
        final.append(
            ToolResult(
                tool_call_id=results[i].tool_call_id,
                content=content,
                is_error=results[i].is_error,
            )
        )
    return final


def _try_spill(
    r: ToolResult,
    new_contents: list[str | None],
    index: int,
    state: ContentReplacementState,
    session: SessionContext,
) -> bool:
    """尝试将工具结果落盘"""
    cid = r.tool_call_id
    content = r.content
    nbytes = len(content.encode("utf-8"))

    def decide() -> tuple[str, str]:
        try:
            spill_single(session, cid, content)
        except OSError:
            return ("skip", "")
        path = str(Path(session.spill_dir) / cid)
        return ("replaced", build_preview(nbytes, _head_preview(content), path))

    result = state.decide_once(cid, content, decide)
    new_contents[index] = result
    return result != content
