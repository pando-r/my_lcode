"""Provider selection helpers for the TUI."""

from __future__ import annotations

from lcode.config import ProviderConfig


def option_label(cfg: ProviderConfig) -> str:
    # The label shown for a provider in the selection list.
    return f"{cfg.name}  ({cfg.model})"
