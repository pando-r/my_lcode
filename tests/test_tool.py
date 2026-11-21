from __future__ import annotations

import asyncio
import json

from lcode.tool import Registry, Result, new_default_registry
from lcode.tool.bash import BashTool
from lcode.tool.edit_file import EditFileTool
from lcode.tool.glob_tool import GlobTool
from lcode.tool.grep_tool import GrepTool
from lcode.tool.read_file import ReadFileTool
from lcode.tool.write_file import WriteFileTool


def test_registry_definitions():
    reg = new_default_registry()
    defs = reg.definitions()
    assert len(defs) == 7
    assert [d.name for d in defs] == [
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "glob",
        "grep",
        "notify",
    ]
    assert reg.get("read_file") is not None
    assert reg.get("nope") is None


def test_read_only_classification():
    reg = new_default_registry()
    assert [d.name for d in reg.read_only_definitions()] == ["read_file", "glob", "grep"]
    assert reg.is_read_only("read_file") is True
    assert reg.is_read_only("glob") is True
    assert reg.is_read_only("write_file") is False
    assert reg.is_read_only("bash") is False
    assert reg.is_read_only("nope") is False  # unknown tool


async def test_read_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello\nworld\n", encoding="utf-8")
    r = await ReadFileTool().execute(json.dumps({"path": str(p)}))
    assert not r.is_error
    assert "hello" in r.content
    assert "     1" in r.content  # line number prefix


async def test_read_file_missing():
    r = await ReadFileTool().execute(json.dumps({"path": "/no/such/file"}))
    assert r.is_error


async def test_write_file(tmp_path):
    p = tmp_path / "a" / "b" / "c.txt"
    r = await WriteFileTool().execute(json.dumps({"path": str(p), "content": "hi"}))
    assert not r.is_error
    assert p.read_text(encoding="utf-8") == "hi"


async def test_edit_file_three_cases(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("hello world", encoding="utf-8")

    r = await EditFileTool().execute(
        json.dumps({"path": str(p), "old_string": "world", "new_string": "there"})
    )
    assert not r.is_error
    assert p.read_text(encoding="utf-8") == "hello there"

    r = await EditFileTool().execute(
        json.dumps({"path": str(p), "old_string": "zzz", "new_string": "x"})
    )
    assert r.is_error and "未找到" in r.content

    p.write_text("a a a", encoding="utf-8")
    r = await EditFileTool().execute(
        json.dumps({"path": str(p), "old_string": "a", "new_string": "b"})
    )
    assert r.is_error and "不唯一" in r.content


async def test_bash_echo():
    r = await BashTool().execute(json.dumps({"command": "echo hi"}))
    assert not r.is_error
    assert "hi" in r.content


async def test_timeout():
    class SlowTool:
        read_only: bool = False

        def name(self) -> str:
            return "slow"

        def description(self) -> str:
            return "slow"

        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        async def execute(self, args: str) -> Result:
            await asyncio.sleep(10)
            return Result(content="done")

    reg = Registry()
    reg.register(SlowTool())
    r = await reg.execute("slow", "{}", timeout=0.1)
    assert r.is_error
    assert "超时" in r.content


async def test_glob(tmp_path):
    (tmp_path / "x.py").write_text("", encoding="utf-8")
    (tmp_path / "y.txt").write_text("", encoding="utf-8")
    r = await GlobTool().execute(json.dumps({"pattern": "*.py", "path": str(tmp_path)}))
    assert not r.is_error
    assert "x.py" in r.content
    assert "y.txt" not in r.content


async def test_grep(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    return 42\n", encoding="utf-8")
    r = await GrepTool().execute(json.dumps({"pattern": "def foo", "path": str(tmp_path)}))
    assert not r.is_error
    assert "a.py" in r.content
    assert "def foo" in r.content


def test_registry_count():
    assert Registry().count() == 0
    assert new_default_registry().count() == 7
