from __future__ import annotations

import json

from mewcode.llm import Message, StreamEvent
from mewcode.memory import Manager, Store, UpdateAction, parse_actions


def _action(**overrides) -> UpdateAction:
    data = {
        "action": "create",
        "level": "project",
        "type": "project_knowledge",
        "title": "T",
        "slug": "t",
        "content": "body",
    }
    data.update(overrides)
    action = UpdateAction.from_dict(data)
    assert action is not None
    return action


def test_store_create_note(tmp_path):
    store = Store(str(tmp_path / "mem"))
    store.apply([_action()])

    note = tmp_path / "mem" / "project_knowledge_t.md"
    assert note.is_file()
    text = note.read_text(encoding="utf-8")
    assert "type: project_knowledge" in text
    assert "title: T" in text
    assert "created:" in text

    index = (tmp_path / "mem" / "MEMORY.md").read_text(encoding="utf-8")
    assert "- [project_knowledge] T — body" in index


def test_store_update_note(tmp_path):
    store = Store(str(tmp_path / "mem"))
    store.apply([_action()])
    store.apply(
        [
            UpdateAction.from_dict(
                {
                    "action": "update",
                    "level": "project",
                    "filename": "project_knowledge_t.md",
                    "title": "T2",
                    "content": "new body",
                }
            )
        ]
    )

    text = (tmp_path / "mem" / "project_knowledge_t.md").read_text(encoding="utf-8")
    assert "title: T2" in text
    assert "new body" in text

    index = (tmp_path / "mem" / "MEMORY.md").read_text(encoding="utf-8")
    assert "- [project_knowledge] T — body" not in index
    assert "- [project_knowledge] T2 — new body" in index


def test_store_delete_note(tmp_path):
    store = Store(str(tmp_path / "mem"))
    store.apply([_action()])
    store.apply(
        [
            UpdateAction.from_dict(
                {
                    "action": "delete",
                    "level": "project",
                    "filename": "project_knowledge_t.md",
                }
            )
        ]
    )

    assert not (tmp_path / "mem" / "project_knowledge_t.md").exists()
    index = (tmp_path / "mem" / "MEMORY.md").read_text(encoding="utf-8")
    assert "project_knowledge" not in index


def test_store_rejects_path_traversal(tmp_path):
    store = Store(str(tmp_path / "mem"))
    store.apply(
        [
            _action(filename="../escape.md"),
            _action(action="delete", filename="../../boom.md"),
        ]
    )
    assert not (tmp_path / "escape.md").exists()
    assert not (tmp_path.parent / "boom.md").exists()


def test_manager_load_index_order(tmp_path):
    project = Store(str(tmp_path / "project"))
    user = Store(str(tmp_path / "user"))
    project.apply([_action(title="PROJECT_NOTE")])
    user.apply(
        [
            _action(
                level="user",
                type="user_preference",
                title="USER_NOTE",
                slug="u",
                content="pref",
            )
        ]
    )

    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    text = manager.load_index()
    assert text.index("[project_knowledge] PROJECT_NOTE") < text.index(
        "[user_preference] USER_NOTE"
    )


def test_manager_truncates_index(tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "user").mkdir()
    (tmp_path / "project" / "MEMORY.md").write_text(
        ("- [project_knowledge] long — " + "x" * 100 + "\n") * 1000,
        encoding="utf-8",
    )
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    text = manager.load_index()
    assert "(index truncated)" in text
    assert len(text.encode("utf-8")) < 25 * 1024 + 100


class FakeProvider:
    name = "fake"
    model = "fake"

    def __init__(self, text: str = "", err: Exception | None = None) -> None:
        self._text = text
        self._err = err
        self.requests: list = []

    async def stream(self, req):
        self.requests.append(req)
        if self._err is not None:
            yield StreamEvent(err=self._err)
            return
        if self._text:
            yield StreamEvent(text=self._text)
        yield StreamEvent(done=True)


async def test_manager_update_async_creates_note(tmp_path):
    payload = json.dumps(
        [
            {
                "action": "create",
                "level": "project",
                "type": "project_knowledge",
                "title": "API conventions",
                "slug": "api_conventions",
                "content": "Use snake_case.",
            }
        ]
    )
    provider = FakeProvider(payload)
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"), provider, "fake")

    await manager.update_async([Message(role="user", content="记住：API 用 snake_case")])

    assert (tmp_path / "project" / "project_knowledge_api_conventions.md").is_file()
    assert len(provider.requests) == 1
    assert provider.requests[0].tools == []
    assert provider.requests[0].messages[0].role == "user"


async def test_manager_update_failure_is_silent(tmp_path):
    provider = FakeProvider(err=RuntimeError("boom"))
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"), provider, "fake")

    await manager.update_async([Message(role="user", content="记住")])
    notes = list((tmp_path / "project").glob("*.md")) if (tmp_path / "project").exists() else []
    assert notes == []


async def test_manager_update_without_provider_is_noop(tmp_path):
    manager = Manager(str(tmp_path / "project"), str(tmp_path / "user"))
    await manager.update_async([Message(role="user", content="记住")])
    assert not (tmp_path / "project").exists()


def test_parse_actions_strips_code_fences():
    raw = (
        "```json\n"
        '[{"action":"create","level":"user","type":"user_preference",'
        '"title":"Terse","slug":"terse","content":"Be brief."}]\n'
        "```"
    )
    actions = parse_actions(raw)
    assert len(actions) == 1
    assert actions[0].level == "user"


def test_parse_actions_empty_array():
    assert parse_actions("[]") == []


def test_manager_list_files_levels_and_sorting(tmp_path):
    project = tmp_path / "project"
    user = tmp_path / "user"
    project.mkdir()
    user.mkdir()
    (project / "MEMORY.md").write_text("", encoding="utf-8")
    (project / "b_note.md").write_text("", encoding="utf-8")
    (project / "a_note.md").write_text("", encoding="utf-8")
    (project / "ignore.txt").write_text("", encoding="utf-8")
    (user / "prefs.md").write_text("", encoding="utf-8")

    manager = Manager(str(project), str(user))
    project_files, user_files = manager.list_files()
    assert project_files == ["MEMORY.md", "a_note.md", "b_note.md"]
    assert user_files == ["prefs.md"]


def test_manager_list_files_missing_dirs(tmp_path):
    manager = Manager(str(tmp_path / "nope"), str(tmp_path / "also-nope"))
    assert manager.list_files() == ([], [])
