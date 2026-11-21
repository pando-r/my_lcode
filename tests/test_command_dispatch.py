from __future__ import annotations

import pytest

from lcode.command import parse


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ("", False)),
        ("   ", ("", False)),
        ("hello", ("", False)),
        ("/", ("", True)),
        ("/help", ("help", True)),
        ("  /HELP  ", ("help", True)),
        ("/help xx", ("", True)),
        ("/help  ", ("help", True)),
        ("//double", ("/double", True)),
        ("/ /help", ("", True)),
        ("/s", ("s", True)),
    ],
)
def test_parse(text, expected):
    assert parse(text) == expected
