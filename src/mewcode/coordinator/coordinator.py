"""Compatibility module: Coordinator Mode helpers live in the package root."""

from mewcode.coordinator import (
    COORDINATOR_ALLOWED_TOOLS,
    SYSTEM_PROMPT_SUFFIX,
    allowed_tools,
    env_truthy,
    is_enabled,
    system_prompt_suffix,
)

__all__ = [
    "COORDINATOR_ALLOWED_TOOLS",
    "SYSTEM_PROMPT_SUFFIX",
    "allowed_tools",
    "env_truthy",
    "is_enabled",
    "system_prompt_suffix",
]
