"""Knowledge units: deterministic drafting and (de)serialization.

Deterministic extraction cannot invent explanations, so it produces *draft* units
that carry verbatim material only. Drafts are marked `draft: true`; the
`auto_unit_id` and `draft_sections` gates downgrade their structural violations
from blocker to major, because a draft is a placeholder for human/model work, not
a finished section. See docs/unit-schema.md §5 and the note added in
docs/architecture.md §19.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from .ids import ascii_slug
from .ir import sha256_text, write_jsonl

UNIT_TYPES = {"concept", "property", "algorithm", "example", "pitfall", "procedure", "summary"}

# sections a type must provide (docs/unit-schema.md §5.1)
REQUIRED_SECTIONS = {
    "concept": ["定义", "说明"],
    "property": ["陈述", "依据", "用途"],
    "algorithm": ["问题", "步骤", "复杂度", "例子"],
    "example": ["题目", "解法", "验证"],
    "pitfall": ["错误说法", "正确说法", "原因"],
    "procedure": ["适用条件", "步骤", "示例"],
    "summary": ["要点"],
}


@dataclass
class Unit:
    id: str
    title: str
    type: str = "concept"
    evidence: list[dict[str, Any]] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    see_also: list[str] = field(default_factory=list)
    symbols: list[dict[str, str]] = field(default_factory=list)
    origin: str = "source"
    emphasis: bool = False
    revision: int = 1
    suspects: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    draft: bool = True
    sections: dict[str, list[str]] = field(default_factory=dict)
    schema_version: int = 1

    # -- serialization ---------------------------------------------------
    def frontmatter(self) -> dict[str, Any]:
        fm: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "evidence": self.evidence,
            "origin": self.origin,
            "revision": self.revision,
        }
        if self.depends_on:
            fm["depends_on"] = self.depends_on
        if self.see_also:
            fm["see_also"] = self.see_also
        if self.symbols:
            fm["symbols"] = self.symbols
        if self.emphasis:
            fm["emphasis"] = True
        if self.suspects:
            fm["suspects"] = self.suspects
        if self.aliases:
            fm["aliases"] = self.aliases
        if self.draft:
            fm["draft"] = True
        return fm

    def to_markdown(self) -> str:
        fm = yaml.safe_dump(self.frontmatter(), allow_unicode=True, sort_keys=False).strip()
        parts = [f"---\n{fm}\n---", ""]
        for name, lines in self.sections.items():
            parts.append(f"## {name}")
            parts.append("")
            parts.extend(lines)
            parts.append("")
        return "\n".join(parts).rstrip() + "\n"

    @property
    def content_hash(self) -> str:
        return sha256_text(self.to_markdown())

    # -- parsing ---------------------------------------------------------
    @classmethod
    def from_markdown(cls, text: str) -> "Unit":
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not m:
            raise ValueError("unit file has no frontmatter")
        fm = yaml.safe_load(m.group(1)) or {}
        body = m.group(2)
        sections: dict[str, list[str]] = {}
        current: str | None = None
        for line in body.splitlines():
            hm = re.match(r"^##\s+(.*)$", line)
            if hm:
                current = hm.group(1).strip()
                sections.setdefault(current, [])
                continue
            if current is not None:
                sections[current].append(line)
        for k in sections:
            while sections[k] and not sections[k][-1].strip():
                sections[k].pop()
        return cls(
            id=fm["id"],
            title=fm["title"],
            type=fm.get("type", "concept"),
            evidence=fm.get("evidence", []),
            depends_on=fm.get("depends_on", []),
            see_also=fm.get("see_also", []),
            symbols=fm.get("symbols", []),
            origin=fm.get("origin", "source"),
            emphasis=bool(fm.get("emphasis", False)),
            revision=int(fm.get("revision", 1)),
            suspects=fm.get("suspects", []),
            aliases=fm.get("aliases", []),
            draft=bool(fm.get("draft", False)),
            sections=sections,
            schema_version=int(fm.get("schema_version", 1)),
        )


def load_units(units_dir: Path) -> list[Unit]:
    units: list[Unit] = []
    for p in sorted(units_dir.glob("*.md")):
        units.append(Unit.from_markdown(p.read_text(encoding="utf-8")))
    return units


def save_unit(units_dir: Path, unit: Unit) -> None:
    units_dir.mkdir(parents=True, exist_ok=True)
    (units_dir / f"{unit.id}.md").write_text(unit.to_markdown(), encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------
# deterministic drafting
# --------------------------------------------------------------------------

def loc_index(block: dict[str, Any]) -> int | None:
    """Position of a block within its source.

    `slide` for decks, `page` for PDFs, `para` for Word documents — every adapter
    has exactly one monotone position, and grouping must not assume which.
    """
    loc = block.get("loc") or {}
    for key in ("slide", "page", "para"):
        if isinstance(loc.get(key), int):
            return loc[key]
    return None


def _group_by_slide(blocks: list[dict[str, Any]]) -> list[tuple[int, list[dict[str, Any]]]]:
    groups: list[tuple[int, list[dict[str, Any]]]] = []
    for b in blocks:
        slide = loc_index(b)
        if slide is None:
            # tables and inline images have no positional index of their own; they
            # belong to whatever unit was being read when they appeared
            if groups:
                groups[-1][1].append(b)
            continue
        if groups and groups[-1][0] == slide:
            groups[-1][1].append(b)
        else:
            groups.append((slide, [b]))
    return groups


# "6.1 树的定义", "0.1指针的本质" (no space before CJK), "2. 链式存储结构"
HEADING_RE = re.compile(
    r"^\s*(?:"
    r"第\s*\d+\s*[章节]"
    r"|\d+(?:\.\d+)+(?=\s|[^\x00-\x7f])"   # dotted numbering, space optional before CJK
    r"|\d+\.\s"                            # "2. 标题"
    r")"
)
CHAPTER_RE = re.compile(r"^\s*第\s*[0-9]+\s*[章节]")

# single-letter node labels from diagrams: "A", "B", "T1", "n>1", ". . ."
LABEL_RE = re.compile(r"^[A-Za-z]?\d{0,3}$|^[A-Za-z]{1,2}\d{0,2}$")
NOISE_RE = re.compile(r"^[\s.·…\-—_=~]*$")


def clean_text(text: str) -> str:
    """Collapse runs of whitespace; decks pad headings with wide gaps."""
    return re.sub(r"\s+", " ", text or "").strip()


def _is_diagram_label(text: str) -> bool:
    t = clean_text(text)
    if not t or len(t) > 4:
        return False
    return bool(LABEL_RE.match(t) or NOISE_RE.match(t))


def _slide_title(blocks: list[dict[str, Any]]) -> str | None:
    for b in blocks:
        if b.get("type") == "title":
            text = (b.get("text") or "").strip()
            if text:
                return text
    return None


def _is_section_start(blocks: list[dict[str, Any]]) -> str | None:
    """Only *numbered* headings open a section.

    Decks frequently use full sentences as slide titles ("树是 n 个结点的有限集"),
    which are content, not structure. Treating every title as a boundary yields one
    unit per slide. Numbered headings ("6.1 树的定义和基本术语") are the reliable
    structural signal.
    """
    title = _slide_title(blocks)
    if not title:
        return None
    if CHAPTER_RE.match(title) or HEADING_RE.match(title):
        return title
    return None


def draft_units(
    blocks_by_src: dict[str, list[dict[str, Any]]],
    *,
    src_to_lecture: dict[str, int],
    include_notes: bool = False,
) -> list[Unit]:
    """Group slides into draft units by section headings.

    A unit spans consecutive slides from one heading slide up to the slide before
    the next heading. Slides before the first heading attach to a synthetic
    opening unit.
    """
    units: list[Unit] = []
    used_ids: dict[str, int] = {}

    for src, blocks in blocks_by_src.items():
        if src.endswith((".yaml", ".yml")) and not include_notes:
            continue
        groups = _group_by_slide(blocks)
        if not groups:
            continue

        buckets: list[tuple[str | None, list[tuple[int, list[dict]]]]] = []
        for slide, sb in groups:
            heading = _is_section_start(sb)
            if heading:
                # decks repeat the running heading on every slide of a section
                if buckets and buckets[-1][0] == heading:
                    buckets[-1][1].append((slide, sb))
                else:
                    buckets.append((heading, [(slide, sb)]))
            elif buckets:
                buckets[-1][1].append((slide, sb))
            else:
                buckets.append((None, [(slide, sb)]))

        for title, slides in buckets:
            unit = _build_unit(src, title, slides, src_to_lecture, used_ids)
            if unit is not None:
                units.append(unit)

    return units


def _build_unit(src: str, title: str | None, slides, src_to_lecture, used_ids) -> Unit | None:
    first_slide = slides[0][0]
    last_slide = slides[-1][0]
    if title is None:
        # a cover slide has no heading; "第N章 …" among its bullets names the lecture
        for _, sb in slides:
            for b in sb:
                if b.get("type") == "bullet" and CHAPTER_RE.match(clean_text(b.get("text") or "")):
                    title = clean_text(b["text"])
                    break
            if title:
                break
    display_title = clean_text(title) or f"第 {first_slide} 页起"
    base = ascii_slug(display_title)
    if base in used_ids:
        used_ids[base] += 1
        uid = f"{base}-{used_ids[base]}"
    else:
        used_ids[base] = 1
        uid = base

    bullets: list[str] = []
    leads: list[str] = []
    formulas: list[str] = []
    images: list[str] = []
    tables: list[str] = []
    notes: list[str] = []
    evidence: list[dict[str, Any]] = []

    diagram_labels: list[str] = []
    image_notes: list[str] = []

    for slide_no, sb in slides:
        lead = clean_text(_slide_title(sb) or "")
        if lead and lead != display_title and (not leads or leads[-1] != f"**{lead}**"):
            leads.append(f"**{lead}**")
        for b in sb:
            t = b.get("type")
            if t == "title":
                continue
            if t == "bullet" and b.get("text"):
                text = clean_text(b["text"])
                if not text:
                    continue
                if _is_diagram_label(text):
                    # node labels from a figure, not prose (docs/ingest.md §4.2)
                    if text not in diagram_labels:
                        diagram_labels.append(text)
                    continue
                bullets.append(text)
                evidence.append({"src": b["src"], "loc": b.get("loc"), "block": b.get("id")})
            elif t == "formula" and b.get("latex"):
                formulas.append(f"$$ {b['latex']} $$")
                evidence.append({"src": b["src"], "loc": b.get("loc"), "block": b.get("id")})
            elif t == "image" and b.get("asset"):
                images.append(b["asset"])
                if b.get("semantics"):
                    sem = b["semantics"]
                    if isinstance(sem, dict):
                        note = sem.get("summary") or sem.get("text") or ""
                        rel = sem.get("relations")
                        if rel:
                            note += " 关系：" + "；".join(f"{a}→{b_}" for a, b_ in rel)
                    else:
                        note = str(sem)
                    if note:
                        image_notes.append(f"- {note}")
                evidence.append({"src": b["src"], "loc": b.get("loc"), "block": b.get("id")})
            elif t == "table" and b.get("rows"):
                rows = b["rows"]
                header = "| " + " | ".join(str(c) for c in rows[0]) + " |"
                sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
                body = ["| " + " | ".join(str(c) for c in r) + " |" for r in rows[1:]]
                tables.append("\n".join([header, sep, *body]))
                evidence.append({"src": b["src"], "loc": b.get("loc"), "block": b.get("id")})
            if b.get("speaker_notes"):
                notes.append(b["speaker_notes"].strip())

    if not (bullets or formulas or tables or images or notes):
        return None  # slide carried nothing extractable: do not emit an empty unit

    if not evidence:
        evidence = [{"src": src, "loc": {"slide": first_slide}}]

    sections: dict[str, list[str]] = {}
    if bullets:
        sections["定义"] = [bullets[0]]
    elif leads:
        sections["定义"] = [leads[0]]
    elif formulas:
        sections["定义"] = ["（待补充）"]
    else:
        sections["定义"] = ["（本节无可提取文字）"]

    rest = [f"- {x}" for x in bullets[1:]] if bullets else []
    body = leads + rest
    if body:
        sections["说明"] = body

    if diagram_labels and len(diagram_labels) >= 3:
        sections.setdefault("图示标注", ["、".join(diagram_labels)])

    if formulas:
        sections["算式"] = formulas
    if tables:
        sections["表格"] = tables
    if images:
        sections["图示"] = [f"[[asset:{Path(a).stem}]]" for a in images]
    if image_notes:
        sections["图示说明"] = image_notes
    if notes:
        sections["备注"] = notes

    return Unit(
        id=uid,
        title=display_title,
        type="concept",
        evidence=evidence,
        origin="source",
        draft=True,
        sections=sections,
    )
