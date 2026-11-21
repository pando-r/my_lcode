from __future__ import annotations

from pathlib import Path

from lcode.instructions import Loader


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _loader(root: Path, home: Path) -> Loader:
    return Loader(project_root=str(root), user_home=str(home))


def test_three_layer_priority(tmp_path):
    root = tmp_path / "project"
    home = tmp_path / "home"
    _write(root / "LCODE.md", "PROJECT_ROOT")
    _write(root / ".lcode" / "LCODE.md", "PROJECT_CONFIG")
    _write(home / ".lcode" / "LCODE.md", "USER_LEVEL")

    text = _loader(root, home).load()
    assert text.index("PROJECT_ROOT") < text.index("PROJECT_CONFIG") < text.index("USER_LEVEL")


def test_missing_files_are_silent(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _write(root / "LCODE.md", "ONLY")

    text = _loader(root, tmp_path / "home").load()
    assert text.strip() == "ONLY"
    assert "已跳过" not in text


def test_include_expansion(tmp_path):
    root = tmp_path / "project"
    _write(root / "LCODE.md", "TOP\n@include sub/rules.md\nEND")
    _write(root / "sub" / "rules.md", "RULES_CONTENT")

    text = _loader(root, tmp_path / "home").load()
    assert "RULES_CONTENT" in text
    assert "@include sub/rules.md" not in text
    assert text.index("TOP") < text.index("RULES_CONTENT") < text.index("END")


def test_nested_include(tmp_path):
    root = tmp_path / "project"
    _write(root / "LCODE.md", "@include a.md")
    _write(root / "a.md", "A\n@include b.md")
    _write(root / "b.md", "B\n@include c.md")
    _write(root / "c.md", "C_CONTENT")

    text = _loader(root, tmp_path / "home").load()
    assert "C_CONTENT" in text


def test_depth_limit(tmp_path):
    root = tmp_path / "project"
    _write(root / "LCODE.md", "@include a1.md")
    for i in range(1, 5):
        _write(root / f"a{i}.md", f"LEVEL{i}\n@include a{i + 1}.md")
    _write(root / "a5.md", "LEVEL5")

    text = _loader(root, tmp_path / "home").load()
    assert "LEVEL4" in text
    assert "LEVEL5" not in text
    assert "超过最大嵌套深度" in text


def test_loop_detection(tmp_path):
    root = tmp_path / "project"
    _write(root / "LCODE.md", "TOP\n@include a.md")
    _write(root / "a.md", "A\n@include LCODE.md")

    text = _loader(root, tmp_path / "home").load()
    assert "检测到环路" in text
    assert text.count("TOP") == 1


def test_path_escape(tmp_path):
    root = tmp_path / "project"
    _write(tmp_path / "outside.md", "SECRET")
    _write(root / "LCODE.md", "@include ../outside.md")

    text = _loader(root, tmp_path / "home").load()
    assert "SECRET" not in text
    assert "路径超出允许范围" in text


def test_binary_file_skipped(tmp_path):
    root = tmp_path / "project"
    _write(root / "LCODE.md", "@include bin.md")
    (root / "bin.md").write_bytes(b"abc\x00def")

    text = _loader(root, tmp_path / "home").load()
    assert "二进制文件" in text


def test_inline_include_not_expanded(tmp_path):
    root = tmp_path / "project"
    _write(root / "LCODE.md", "see @include rules.md here")
    _write(root / "rules.md", "RULES_CONTENT")

    text = _loader(root, tmp_path / "home").load()
    assert "RULES_CONTENT" not in text
    assert "see @include rules.md here" in text
