"""Adapter contract (docs/ingest.md §2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ir import Block


@dataclass
class IngestResult:
    blocks: list[Block] = field(default_factory=list)
    assets: list[tuple[Path, str]] = field(default_factory=list)  # (source, asset_rel_path)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)


class Adapter:
    name = "base"
    accepts: tuple[str, ...] = ()

    def probe(self, path: Path) -> dict[str, Any]:
        return {"present": path.exists(), "size": path.stat().st_size if path.exists() else 0}

    def ingest(self, path: Path, ctx: Any) -> IngestResult:  # pragma: no cover - interface
        raise NotImplementedError
