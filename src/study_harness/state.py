"""Workspace state and content-addressed caching (docs/state.md)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import PIPELINE_VERSION, SCHEMA_VERSION

STATE_FILE = ".study/state.json"

STAGE_ORDER = ["ingest", "probe", "align", "units", "outline", "render", "gates"]


@dataclass
class State:
    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, workspace: Path) -> "State":
        p = workspace / STATE_FILE
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
        else:
            data = {
                "schema_version": SCHEMA_VERSION,
                "pipeline_version": PIPELINE_VERSION,
                "stages": {},
                "inputs": {},
                "modifications": [],
            }
        return cls(path=p, data=data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["pipeline_version"] = PIPELINE_VERSION
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    # -- inputs -----------------------------------------------------------
    def record_inputs(self, files: list[Path]) -> None:
        from .ir import sha256_file

        self.data.setdefault("inputs", {})
        for f in files:
            rel = f.name
            self.data["inputs"][rel] = {
                "sha256": sha256_file(f),
                "size": f.stat().st_size,
            }

    def inputs_hash(self) -> str:
        payload = json.dumps(self.data.get("inputs", {}), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    # -- stages -----------------------------------------------------------
    def get_stage(self, name: str) -> dict[str, Any]:
        return self.data.setdefault("stages", {}).get(name, {})

    def mark(self, name: str, status: str, key: str | None = None, **extra: Any) -> None:
        stage = {"status": status}
        if key:
            stage["key"] = key
        stage.update(extra)
        self.data.setdefault("stages", {})[name] = stage

    def is_fresh(self, name: str, key: str) -> bool:
        stage = self.get_stage(name)
        return stage.get("status") in {"done", "partial"} and stage.get("key") == key

    def record_modification(self, source: str, unit: str, before: int, after: int, **extra: Any) -> None:
        self.data.setdefault("modifications", []).append(
            {"source": source, "unit": unit, "revision_from": before, "revision_to": after, **extra}
        )
