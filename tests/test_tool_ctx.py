from __future__ import annotations

import json
from pathlib import Path

from mewcode.tool.bash import BashTool
from mewcode.tool.ctx import cwd_from_ctx, resolve_path, with_cwd
from mewcode.tool.edit_file import EditFileTool
from mewcode.tool.glob_tool import GlobTool
from mewcode.tool.grep_tool import GrepTool
from mewcode.tool.read_file import ReadFileTool
from mewcode.tool.write_file import WriteFileTool


def test_cwd_context_roundtrip(tmp_path):
    assert cwd_from_ctx() is None
    with with_cwd(str(tmp_path)):
        assert cwd_from_ctx() == str(tmp_path)
    assert cwd_from_ctx() is None


def test_with_empty_cwd_is_noop(tmp_path):
    with with_cwd(str(tmp_path)):
        with with_cwd(""):
            assert cwd_from_ctx() == str(tmp_path)


def test_resolve_path(tmp_path):
    with with_cwd(str(tmp_path)):
        assert resolve_path("a.txt") == str(tmp_path / "a.txt")
        assert resolve_path("") == str(tmp_path)
        absolute = str(tmp_path / "abs.txt")
        assert resolve_path(absolute) == absolute


def test_resolve_path_falls_back_to_process_cwd():
    assert resolve_path("a.txt") == str(Path.cwd() / "a.txt")
    assert resolve_path("") == str(Path.cwd())


async def test_read_write_edit_use_ctx_cwd(tmp_path):
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    with with_cwd(str(tmp_path)):
        result = await ReadFileTool().execute(json.dumps({"path": "a.txt"}))
        assert "hello world" in result.content

        result = await WriteFileTool().execute(json.dumps({"path": "sub/b.txt", "content": "new"}))
        assert not result.is_error
        assert (tmp_path / "sub" / "b.txt").read_text(encoding="utf-8") == "new"

        result = await EditFileTool().execute(
            json.dumps({"path": "a.txt", "old_string": "world", "new_string": "there"})
        )
        assert not result.is_error
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello there"


async def test_glob_and_grep_use_ctx_cwd(tmp_path):
    (tmp_path / "x.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    (tmp_path / "y.txt").write_text("nothing\n", encoding="utf-8")
    with with_cwd(str(tmp_path)):
        result = await GlobTool().execute(json.dumps({"pattern": "*.py"}))
        assert "x.py" in result.content and "y.txt" not in result.content

        result = await GrepTool().execute(json.dumps({"pattern": "def foo"}))
        assert "x.py" in result.content
        assert "def foo" in result.content


async def test_bash_uses_ctx_cwd(tmp_path):
    with with_cwd(str(tmp_path)):
        result = await BashTool().execute(json.dumps({"command": "cd"}))
    assert not result.is_error
    assert str(tmp_path) in result.content
