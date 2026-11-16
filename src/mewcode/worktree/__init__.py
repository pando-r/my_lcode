"""Git worktree isolation: manager, lifecycle types, and slug helpers."""

from mewcode.worktree.lifecycle import (
    AutoCleanupReport,
    ExitAction,
    ExitOptions,
    ExitReport,
    WorktreeHasChangesError,
)
from mewcode.worktree.manager import DEFAULT_SYMLINK_DIRS, Manager, Worktree
from mewcode.worktree.session import WorktreeSession
from mewcode.worktree.slug import flat_slug, validate_slug
from mewcode.worktree.sweep import random_agent_name

__all__ = [
    "DEFAULT_SYMLINK_DIRS",
    "AutoCleanupReport",
    "ExitAction",
    "ExitOptions",
    "ExitReport",
    "Manager",
    "Worktree",
    "WorktreeHasChangesError",
    "WorktreeSession",
    "flat_slug",
    "random_agent_name",
    "validate_slug",
]
