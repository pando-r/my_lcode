from __future__ import annotations

import pytest

from lcode.worktree.slug import flat_slug, validate_slug


@pytest.mark.parametrize(
    "name",
    ["alice", "team/alice", "v1.0", "a_b", "a-b", "a.b.c", "x" * 64],
)
def test_valid_slugs(name):
    validate_slug(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "x" * 65,
        "..",
        ".",
        "../etc",
        "./x",
        "a//b",
        "/x",
        "a/",
        "a b",
        "a;b",
        "a/../b",
    ],
)
def test_invalid_slugs(name):
    with pytest.raises(ValueError):
        validate_slug(name)


def test_flat_slug():
    assert flat_slug("team/alice") == "team+alice"
    assert flat_slug("alice") == "alice"
    assert flat_slug("a/b/c") == "a+b+c"
