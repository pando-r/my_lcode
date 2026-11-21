"""Agent role definitions, parsing, and multi-source catalog."""

from lcode.subagent.catalog import Catalog, load_catalog
from lcode.subagent.definition import Definition, ModelName, Source
from lcode.subagent.embed import builtin_definitions
from lcode.subagent.parser import (
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
