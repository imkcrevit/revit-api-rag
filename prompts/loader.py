"""Prompt template loader.

Prompt text lives in this package so prompts can be reviewed and tuned without
digging through runtime code.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Load a prompt template by filename from the prompts directory."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"Invalid prompt name: {name!r}")
    path = _PROMPT_DIR / name
    return path.read_text(encoding="utf-8")
