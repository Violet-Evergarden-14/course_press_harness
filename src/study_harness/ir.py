"""Intermediate representation: blocks with locators (docs/architecture.md §6)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Iterator


BLOCK_TYPES = {
    "title", "bullet", "formula", "table", "image", "speech", "note", "code",
}


@dataclass
class Block:
    id: str
    src: str
    type: str
    loc: dict[str, Any]
    text: str | None = None
    level: int | None = None
    latex: str | None = None
    rows: list[list[str]] | None = None
    asset: str | None = None
    ocr_text: str | None = None
    semantics: Any = None
    semantics_by: str | None = None
    kind: str | None = None
    lang: str | None = None
    marker: str | None = None
    role: str | None = None
    speaker_notes: str | None = None
    low_conf: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        # drop empty optional fields so the JSONL stays readable and diff-stable
        return {k: v for k, v in d.items() if v not in (None, [], {}, "", False) or k in ("id", "src", "type", "loc")}


def write_jsonl(path: Path, blocks: Iterable[Block]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for b in blocks:
            f.write(json.dumps(b.to_json(), ensure_ascii=False, sort_keys=False) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
