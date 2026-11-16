"""Format adapters (docs/ingest.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..ir import Block

ADAPTERS: dict[str, type] = {}


def register(cls):
    ADAPTERS[cls.name] = cls
    return cls


def adapter_for(path: Path):
    from . import (  # noqa: F401  (import registers the adapters)
        docx_adapter,
        media_adapter,
        notes_adapter,
        pdf_adapter,
        pptx_adapter,
        text_adapter,
    )
    ext = path.suffix.lower()
    for cls in ADAPTERS.values():
        if ext in cls.accepts:
            return cls
    return None


__all__ = ["ADAPTERS", "adapter_for", "register", "Block", "Iterable"]
