"""Team-related feature flags."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lcode.config import Config


def fork_teammate_enabled(cfg: Config | None) -> bool:
    features = getattr(cfg, "features", None)
    return bool(getattr(features, "fork_teammate", False))


__all__ = ["fork_teammate_enabled"]
