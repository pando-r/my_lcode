"""Git worktree isolation: manager, lifecycle types, and slug helpers."""

from lcode.worktree.lifecycle import (
    AutoCleanupReport,
    ExitAction,
    ExitOptions,
    ExitReport,
    WorktreeHasChangesError,
)
from lcode.worktree.manager import DEFAULT_SYMLINK_DIRS, Manager, Worktree
from lcode.worktree.session import WorktreeSession
from lcode.worktree.slug import flat_slug, validate_slug
from lcode.worktree.sweep import random_agent_name

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
