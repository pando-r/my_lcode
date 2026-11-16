"""Agent role definitions, parsing, and multi-source catalog."""

from mewcode.subagent.catalog import Catalog, load_catalog
from mewcode.subagent.definition import Definition, ModelName, Source
from mewcode.subagent.embed import builtin_definitions
from mewcode.subagent.parser import (
    AgentParseError,
    parse_definition,
    parse_file,
    parse_frontmatter_and_body,
)

__all__ = [
    "AgentParseError",
    "Catalog",
    "Definition",
    "ModelName",
    "Source",
    "builtin_definitions",
    "load_catalog",
    "parse_definition",
    "parse_file",
    "parse_frontmatter_and_body",
]
