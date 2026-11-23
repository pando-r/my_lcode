"""晨间简报相关的两个底层改动。

1. ``--enqueue-file``：中文指令走 UTF-8 文件入队，避开命令行编码问题。
2. ``gather_environment`` 给出时间与星期：问候语和「今天适合做什么」都依赖它们。

脚本层（scripts/boot_brief*.ps1）没法在 pytest 里跑，但这两个是脚本实际依赖的
Python 契约——脚本坏掉通常是因为它们变了。
"""

from __future__ import annotations

import asyncio
import datetime

import pytest

from lcode.cli import _amain, _build_parser
from lcode.prompt.environment import Environment, _today_label, gather_environment
from lcode.resident.queue import TaskQueue

# ── --enqueue-file ────────────────────────────────────────────────────────


def _run_enqueue_file(tmp_path, monkeypatch, content: str, name: str = "brief.md"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = _build_parser().parse_args(["--enqueue-file", str(path)])
    code = asyncio.run(_amain(args))
    return code, TaskQueue(tmp_path / ".lcode" / "resident")


def test_enqueue_file_queues_utf8_prompt(tmp_path, monkeypatch, capsys):
    """中文内容必须原样进队列。

    这是整条开机简报链路的关键一环：计划任务把命令拼成字符串传进来，
    中文很容易在 OEM 代码页那里变成乱码。走文件读 UTF-8 就绕开了这件事。
    """
    prompt = "你是 K 的贾维斯。\n\n写一份简报：天气、建议。\n"
    code, queue = _run_enqueue_file(tmp_path, monkeypatch, prompt)

    assert code == 0
    tasks = queue.load()
    assert len(tasks) == 1
    assert tasks[0].prompt == prompt.strip()
    assert "贾维斯" in tasks[0].prompt


def test_enqueue_file_relative_path_resolved_from_cwd(tmp_path, monkeypatch):
    (tmp_path / "brief.md").write_text("相对路径任务", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    args = _build_parser().parse_args(["--enqueue-file", "brief.md"])
    assert asyncio.run(_amain(args)) == 0
    tasks = TaskQueue(tmp_path / ".lcode" / "resident").load()
    assert tasks[0].prompt == "相对路径任务"


def test_enqueue_file_missing_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    args = _build_parser().parse_args(["--enqueue-file", "nope.md"])
    assert asyncio.run(_amain(args)) == 2
    assert "读不了指令文件" in capsys.readouterr().err


def test_enqueue_file_rejects_empty(tmp_path, monkeypatch, capsys):
    code, queue = _run_enqueue_file(tmp_path, monkeypatch, "   \n\n")
    assert code == 2
    assert "空的" in capsys.readouterr().err
    assert queue.load() == []


def test_enqueue_file_flag_defaults_to_empty():
    args = _build_parser().parse_args([])
    assert args.enqueue_file == ""


def test_enqueue_flag_still_works(tmp_path, monkeypatch):
    """新入口不能挤掉老的 --enqueue。"""
    monkeypatch.chdir(tmp_path)
    args = _build_parser().parse_args(["--enqueue", "直接给的任务"])
    assert asyncio.run(_amain(args)) == 0
    tasks = TaskQueue(tmp_path / ".lcode" / "resident").load()
    assert tasks[0].prompt == "直接给的任务"


# ── 环境块：时间与星期 ────────────────────────────────────────────────────


def test_environment_includes_time_and_weekday():
    env = gather_environment("0.1.0", "m")
    rendered = env.render()

    assert "Time:" in rendered
    # 问候语靠时间：没有 Time，模型只能说「你好」而不是「早上好」
    assert len(env.time) == 5 and env.time[2] == ":"
    # 时段由代码算好直接给出，模型不用自己从 HH:MM 推
    assert env.time_of_day in ("早上", "上午", "中午", "下午", "晚上", "深夜")
    assert f"({env.time_of_day})" in rendered
    # 星期必须显式给：周六和周一给的建议完全不同
    assert "周" in env.date
    assert "(" in env.date and ")" in env.date


@pytest.mark.parametrize(
    "hour, expected",
    [
        (0, "深夜"),
        (4, "深夜"),
        (5, "早上"),
        (8, "早上"),
        (9, "上午"),
        (11, "上午"),
        (12, "中午"),
        (13, "中午"),
        (14, "下午"),
        (17, "下午"),
        (18, "晚上"),
        (22, "晚上"),
        (23, "深夜"),
    ],
)
def test_time_of_day_boundaries(hour, expected):
    """边界必须明确落在某一档。

    问候语是简报第一句，说错最刺眼；而 11:58 / 12:01 这种边界上不同模型
    给的答案不一样，所以时段在代码里算死。
    """
    from lcode.prompt.environment import _time_of_day

    assert _time_of_day(datetime.datetime(2026, 11, 22, hour, 0)) == expected
    # 同一小时的 59 分不能跳到下一档
    assert _time_of_day(datetime.datetime(2026, 11, 22, hour, 59)) == expected


def test_environment_time_renders_time_of_day():
    env = Environment(
        working_dir="/tmp", platform="linux", date="2026-01-01",
        git_status="", version="0.1.0", model="m", time="14:55", time_of_day="下午",
    )
    assert "Time: 14:55 (下午)" in env.render()


def test_today_label_has_both_weekday_forms():
    # 2026-11-22 是周日
    label = _today_label(datetime.datetime(2026, 11, 22, 9, 30))
    assert label.startswith("2026-11-22")
    assert "Sunday" in label
    assert "周日" in label


@pytest.mark.parametrize(
    "day, en, zh",
    [
        (datetime.datetime(2026, 11, 23), "Monday", "周一"),
        (datetime.datetime(2026, 11, 27), "Friday", "周五"),
        (datetime.datetime(2026, 11, 28), "Saturday", "周六"),
    ],
)
def test_today_label_weekday_mapping(day, en, zh):
    label = _today_label(day)
    assert en in label and zh in label


def test_environment_time_empty_is_omitted():
    # time 默认空串：老构造路径（测试里手动拼 Environment）不该被强制加字段
    env = Environment(
        working_dir="/tmp", platform="linux", date="2026-01-01",
        git_status="", version="0.1.0", model="m",
    )
    assert "Time:" not in env.render()


def test_environment_time_rendered_when_present():
    env = Environment(
        working_dir="/tmp", platform="linux", date="2026-01-01",
        git_status="", version="0.1.0", model="m", time="07:45",
    )
    assert "Time: 07:45" in env.render()


# ── 简报提示词本身 ────────────────────────────────────────────────────────


def test_brief_prompt_file_has_the_required_shape():
    """提示词是脚本的输入，人在改它时最容易删掉关键约束。"""
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / ".lcode" / "brief.md"
    if not path.is_file():
        pytest.skip(".lcode/brief.md 不在仓库里（属于本地配置）")

    text = path.read_text(encoding="utf-8")
    # 三段结构
    assert "打招呼" in text
    assert "天气" in text
    assert "今天可以干什么" in text
    # 不许编数据这一条是硬要求，删了就会开始编天气
    assert "绝不编造" in text or "不准凭记忆编" in text
    # 必须真的去查，而不是凭训练数据回答
    assert "search_web" in text
    # 不出标题/表格：这是横幅通知，不是文档
    assert "Markdown" in text
