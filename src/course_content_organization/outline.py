"""Document outline: lecture-ordered skeleton, auto sectioning, three-way merge
(docs/outline-generation.md)."""

from __future__ import annotations

import json
import re
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ir import sha256_text
from .knowledge import Unit

OUTLINE_FILE = ".study/outline.json"


def _min_slide(unit: Unit) -> int:
    from .knowledge import loc_index

    positions = [p for p in (loc_index(e) for e in unit.evidence) if p is not None]
    return min(positions) if positions else 10**6


def _min_block(unit: Unit) -> int:
    """Position within a slide, from the block id suffix (`s06-012-3`)."""
    for e in unit.evidence:
        block = e.get("block") or ""
        tail = block.rsplit("-", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return 0


def assign_lecture(unit: Unit, src_to_lecture: dict[str, int], default_lecture: int = 1) -> int:
    counts: dict[int, int] = {}
    for e in unit.evidence:
        lec = src_to_lecture.get(e.get("src", ""))
        if lec is not None:
            counts[lec] = counts.get(lec, 0) + 1
    if not counts:
        return default_lecture
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def target_sections(n: int) -> int:
    if n < 3:
        return 1
    return max(2, min(5, round(math.sqrt(n))))


def build_outline(
    units: list[Unit],
    *,
    lecture_titles: dict[int, str],
    src_to_lecture: dict[str, int],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_lecture: dict[int, list[Unit]] = {}
    unit_lecture: dict[str, int] = {}
    for u in units:
        lec = assign_lecture(u, src_to_lecture)
        unit_lecture[u.id] = lec
        by_lecture.setdefault(lec, []).append(u)

    parts: list[dict[str, Any]] = [{"kind": "front_matter", "title": "前言", "sections": []}]

    for lec in sorted(by_lecture):
        us = sorted(by_lecture[lec], key=lambda u: (_min_slide(u), _min_block(u), u.id))
        sections = _auto_sections(us)
        parts.append({
            "kind": "lecture",
            "no": lec,
            "title": lecture_titles.get(lec, f"第 {lec} 讲"),
            "sections": sections,
        })

    outline = {
        "schema_version": 1,
        "parts": parts,
        "_meta": {
            "unit_lecture": unit_lecture,
            "orphans": [],
            "unplaced": [],
        },
    }

    if existing:
        outline, conflicts = merge_outline(existing, outline)
    else:
        conflicts = []

    outline["_meta"]["auto_hash"] = sha256_text(
        json.dumps({p["kind"]: p.get("sections", []) for p in outline["parts"]}, sort_keys=True,
                   ensure_ascii=False)
    )
    return outline, conflicts


# multi-level numbering only: "6.1" is structure, "2." is a list marker
NUM_RE = re.compile(r"^\s*(\d+\.\d+(?:\.\d+)*)")
CHAPTER_TXT_RE = re.compile(r"^\s*第\s*(\d+)\s*[章]")


def heading_level(title: str) -> int | None:
    """Numbering depth of a heading: "6.1" -> 2, "6.2.2" -> 3, "第6章" -> 1.

    Single-level numbers ("2. 示例条目") are treated as absent: in practice they
    are list markers inside a section, and accepting them splits a lecture at every
    enumerated bullet.
    """
    t = (title or "").strip()
    m = NUM_RE.match(t)
    if m:
        return m.group(1).count(".") + 1
    if CHAPTER_TXT_RE.match(t):
        return 1
    return None


def _sections_by_numbering(units: list[Unit]) -> list[dict[str, Any]] | None:
    """Use the deck's own numbering when it carries structure.

    A lecture whose headings are all numbered ("6.1", "6.2.2") already encodes its
    hierarchy, so section boundaries are read off directly instead of guessed from a
    target count. Returns None when numbering is absent or inconsistent.
    """
    # A document that spans several chapters ("第0章 … 第6章") must be sectioned by
    # chapter. A single "第6章" heading is just the title of the lecture and must not
    # become a section, so the top level is decided by how many chapters appear.
    chapter_starts = [i for i, u in enumerate(units) if CHAPTER_TXT_RE.match((u.title or "").strip())]
    if len(chapter_starts) >= 2:
        bounds = chapter_starts + [len(units)]
        sections: list[dict[str, Any]] = []
        for start, end in zip(bounds, bounds[1:]):
            chunk = units[start:end]
            if chunk:
                sections.append({"title": chunk[0].title, "units": [u.id for u in chunk]})
        if units[: chapter_starts[0]] and sections:
            sections[0]["units"] = [u.id for u in units[: chapter_starts[0]]] + sections[0]["units"]
        return sections

    levels = [heading_level(u.title) for u in units]
    # otherwise section structure comes from dotted numbering ("6.1")
    dotted = [heading_level(u.title) if NUM_RE.match((u.title or "").strip()) else None
              for u in units]
    numbered = [l for l in dotted if l is not None]
    if len(numbered) < 3 or len(numbered) < max(3, int(len(units) * 0.5)):
        return None
    top = min(numbered)
    starts = [i for i, l in enumerate(dotted) if l == top]
    if len(starts) < 2:
        return None
    bounds = starts + [len(units)]
    sections: list[dict[str, Any]] = []
    for start, end in zip(bounds, bounds[1:]):
        chunk = units[start:end]
        if not chunk:
            continue
        named = next((u for u in chunk if dotted[units.index(u)] == top), chunk[0])
        sections.append({"title": named.title, "units": [u.id for u in chunk]})
    # material before the first numbered heading (cover, preface) joins section 1
    prefix = units[: starts[0]]
    if prefix and sections:
        sections[0]["units"] = [u.id for u in prefix] + sections[0]["units"]
    return sections


def _auto_sections(units: list[Unit]) -> list[dict[str, Any]]:
    """Sectioning: numbering first, then content-based heuristic
    (docs/outline-generation.md §3)."""
    by_numbering = _sections_by_numbering(units)
    if by_numbering:
        return by_numbering

    n = len(units)
    target = target_sections(n)
    if target == 1:
        return [{"title": units[0].title if units else "内容", "units": [u.id for u in units]}]

    # candidate boundaries: units whose title differs from the previous one
    candidates = [i for i in range(1, n) if units[i].title.strip() != units[i - 1].title.strip()]
    if len(candidates) < target - 1:
        candidates = list(range(1, n))
    if not candidates:
        return [{"title": units[0].title, "units": [u.id for u in units]}]

    # choose target-1 boundaries spread evenly across the candidate list
    picks: list[int] = []
    for k in range(1, target):
        idx = round(k * len(candidates) / target) - 1
        idx = max(0, min(len(candidates) - 1, idx))
        b = candidates[idx]
        if b not in picks:
            picks.append(b)
    picks.sort()

    bounds = [0, *picks, n]
    sections: list[dict[str, Any]] = []
    for start, end in zip(bounds, bounds[1:]):
        chunk = units[start:end]
        if not chunk:
            continue
        sections.append({"title": chunk[0].title, "units": [u.id for u in chunk]})
    return sections


def merge_outline(existing: dict[str, Any], fresh: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Three-way merge: keep human placement, add new units, report orphans."""
    conflicts: list[str] = []
    placed_before: dict[str, tuple[int, int]] = {}
    for pi, part in enumerate(existing.get("parts", [])):
        for si, sec in enumerate(part.get("sections", [])):
            for uid in sec.get("units", []):
                placed_before[uid] = (pi, si)

    fresh_ids = {u for part in fresh["parts"] for sec in part.get("sections", [])
                 for u in sec.get("units", [])}

    # start from existing placement
    merged_parts = json.loads(json.dumps(existing.get("parts", [])))
    present = set(placed_before)
    orphans = sorted(present - fresh_ids)

    # remove orphans
    for part in merged_parts:
        for sec in part.get("sections", []):
            sec["units"] = [u for u in sec.get("units", []) if u in fresh_ids]

    # append units that are new, into their auto section's lecture
    auto_pos = {}
    for part in fresh["parts"]:
        for sec in part.get("sections", []):
            for uid in sec.get("units", []):
                auto_pos[uid] = part

    for uid in sorted(fresh_ids - present):
        target_part = auto_pos.get(uid)
        if not target_part:
            continue
        no = target_part.get("no")
        dest = None
        for part in merged_parts:
            if part.get("kind") == target_part["kind"] and part.get("no") == no:
                dest = part
                break
        if dest is None:
            dest = {"kind": target_part["kind"], "no": no, "title": target_part["title"],
                    "sections": []}
            merged_parts.append(dest)
        if not dest.setdefault("sections", []):
            dest["sections"].append({"title": target_part["sections"][0]["title"], "units": []})
        dest["sections"][-1]["units"].append(uid)

    # drop empty sections but keep explicitly empty ones from existing
    for part in merged_parts:
        part["sections"] = [s for s in part.get("sections", []) if s.get("units") or not s.get("units")]

    if orphans:
        conflicts.append(f"orphans: {', '.join(orphans)}")

    result = {"schema_version": 1, "parts": merged_parts,
              "_meta": {"unit_lecture": fresh["_meta"]["unit_lecture"],
                        "orphans": orphans,
                        "unplaced": []}}
    return result, conflicts


def load_outline(workspace: Path) -> dict[str, Any] | None:
    p = workspace / OUTLINE_FILE
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_outline(workspace: Path, outline: dict[str, Any]) -> None:
    p = workspace / OUTLINE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(outline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
