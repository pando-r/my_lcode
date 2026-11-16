from __future__ import annotations

from mewcode.prompt import Module, assemble_system, build_system_prompt
from mewcode.prompt.environment import gather_environment
from mewcode.prompt.reminder import plan_reminder
from mewcode.tool.bash import BashTool
from mewcode.tool.edit_file import EditFileTool


def test_build_system_prompt_ordered():
    text = build_system_prompt()
    assert "MewCode" in text  # 身份模块
    assert "Prefer dedicated tools" in text  # 工具使用模块
    # 工具使用段在文本输出段之前（优先级 50 < 70）
    assert text.index("Prefer dedicated tools") < text.index("Use Markdown")


def test_assemble_skips_empty():
    mods = [Module("a", 10, "A"), Module("empty", 20, ""), Module("b", 30, "B")]
    assert assemble_system(mods) == "A\n\nB"
    assert "\n\n\n" not in assemble_system(mods)


def test_build_system_prompt_deterministic():
    assert build_system_prompt() == build_system_prompt()


def test_build_system_prompt_injects_optional_modules():
    text = build_system_prompt("INSTRUCTION_TEXT", "MEMORY_INDEX_TEXT")
    assert "INSTRUCTION_TEXT" in text
    assert "MEMORY_INDEX_TEXT" in text
    # 自定义指令（80）在长期记忆（100）之前
    assert text.index("INSTRUCTION_TEXT") < text.index("MEMORY_INDEX_TEXT")
    assert text.rstrip().endswith("MEMORY_INDEX_TEXT")


def test_build_system_prompt_empty_is_backwards_compatible():
    assert build_system_prompt("", "") == build_system_prompt()


def test_double_reinforcement():
    text = build_system_prompt()
    assert "read_file" in text
    assert "edit_file" in text
    # 双重强化：工具描述里也出现
    assert "read_file" in EditFileTool().description()
    assert "read_file" in BashTool().description()


def test_environment_render():
    env = gather_environment("0.1.0", "fake-model")
    rendered = env.render()
    assert "Working Directory" in rendered
    assert "Platform" in rendered
    assert "Date" in rendered
    assert "Version: 0.1.0" in rendered
    assert "Model: fake-model" in rendered


def test_plan_reminder_tagged():
    full = plan_reminder(True)
    concise = plan_reminder(False)
    assert "<system-reminder>" in full
    assert "</system-reminder>" in full
    assert "read-only" in full
    assert len(concise) < len(full)
