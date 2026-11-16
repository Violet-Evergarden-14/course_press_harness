"""Student notes adapter (docs/architecture.md §8).

Notes are an input source, an alignment anchor and a gap signal. They never enter
knowledge units — they live in patches/ and are overlaid at render time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..ir import Block
from . import register
from .base import Adapter, IngestResult

VALID_KINDS = {"emphasis", "question", "correction", "global", "comment", "summary"}


@register
class NotesAdapter(Adapter):
    name = "notes"
    accepts = (".yaml", ".yml")

    def probe(self, path: Path) -> dict[str, Any]:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except Exception:
            return {"present": True, "valid": False}
        items = data if isinstance(data, list) else data.get("notes", [])
        return {"present": True, "items": len(items), "valid": True}

    def ingest(self, path: Path, ctx: Any) -> IngestResult:
        res = IngestResult()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        items = data if isinstance(data, list) else data.get("notes", [])

        for i, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                res.warnings.append({"stage": "notes/schema", "severity": "error",
                                     "msg": "条目不是映射", "index": i})
                continue
            text = (item.get("text") or "").strip()
            kind = (item.get("kind") or "global").strip()
            if not text:
                res.warnings.append({"stage": "notes/schema", "severity": "error",
                                     "msg": "缺少 text", "index": i})
                continue
            if kind not in VALID_KINDS:
                res.warnings.append({"stage": "notes/schema", "severity": "warning",
                                     "msg": f"未知 kind: {kind}", "index": i})
                kind = "comment"

            at = item.get("at") or {}
            loc: dict[str, Any] = {}
            if "slide" in at:
                loc["slide"] = int(at["slide"])
            if "page" in at:
                loc["page"] = int(at["page"])
            if "t" in at:
                loc["t"] = list(at["t"])
            if "unit" in at:
                loc["unit"] = at["unit"]

            res.blocks.append(Block(id=f"nt-{i:04d}", src=path.name, type="note",
                                    loc=loc or {"global": True}, text=text, kind=kind))

        res.capabilities = {"present": True, "items": len(res.blocks)}
        return res
