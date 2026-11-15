"""Deterministic pruning of material that is not teaching content.

Slides carry a lot that must never reach a reader: speaker names, departments,
course URLs, "学习重点" agendas, diagram node labels, duplicated speaker-note text.
Deleting them is mechanical, so it happens before any model sees the material —
otherwise the composer spends its budget rewriting the author's email address.
"""

from __future__ import annotations

import re
from typing import Any

from .knowledge import Unit, clean_text

# admin / cover material
ADMIN_PATTERNS = [
    re.compile(r"^\s*https?://"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    re.compile(r"(?:主讲|讲授|教师|授课|作者|编者|撰稿)\s*[:：]"),
    re.compile(r"(?:大学|学院|系|研究所|实验室)\s*$"),
    re.compile(r"^\s*(?:\d{3,4}[- ]?\d{7,8})\s*$"),          # phone
    re.compile(r"^\s*第\s*\d+\s*讲\s*$"),                     # bare lecture label
]
AGENDA_RE = re.compile(r"^\s*(?:学习重点|本节重点|课程安排|教学目标|考核方式|参考书目)\s*[:：]?\s*$")
# a line that is only symbols or a 1-3 char token: diagram labels, stray glyphs
SYMBOL_ONLY_RE = re.compile(r"^[\W\d_]{1,3}$")
GLYPH_RE = re.compile(r"^(?:[×÷＝=+\-*/<>≤≥≠→←↑↓]|\w{1,2})$")


def is_admin_line(text: str) -> bool:
    t = clean_text(text)
    if not t:
        return True
    if AGENDA_RE.match(t):
        return True
    return any(p.search(t) for p in ADMIN_PATTERNS)


def is_glyph(text: str) -> bool:
    t = clean_text(text).strip("-•· ")
    return bool(t) and (bool(SYMBOL_ONLY_RE.match(t)) or bool(GLYPH_RE.match(t)))


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "")


def _similar(a: str, b: str) -> float:
    """Cheap containment ratio, enough to spot a note pasted from a bullet."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    common = sum(1 for ch in set(na) if ch in nb)
    return common / max(len(set(na)), 1)


def prune_unit(unit: Unit) -> dict[str, Any]:
    """Drop administrative lines, glyphs and duplicated speaker notes.

    Returns a report of what was removed, so pruning is auditable rather than silent.
    """
    dropped: list[str] = []
    dropped = dropped  # per-section dedupe below
    bullet_texts: list[str] = []

    for name in list(unit.sections):
        kept: list[str] = []
        section_seen: set[str] = set()
        for raw in unit.sections[name]:
            text = clean_text(raw.lstrip("- ").strip())
            if not text:
                continue
            if is_admin_line(text) or is_glyph(text):
                dropped.append(text)
                continue
            # a speaker note that restates a bullet adds nothing
            if name == "备注" and any(_similar(text, b) > 0.6 for b in bullet_texts):
                dropped.append(text[:40])
                continue
            key = _norm(text)
            # dedupe inside a section only: dropping a line because *another* section
            # says it would empty the section and destroy structure the author chose
            if key in section_seen and len(key) > 4:
                dropped.append(text[:40])
                continue
            section_seen.add(key)
            if name in {"定义", "说明", "陈述"}:
                bullet_texts.append(text)
            kept.append(raw)
        if kept:
            unit.sections[name] = kept
        else:
            unit.sections.pop(name, None)

    if dropped:
        unit.suspects = list(unit.suspects)
    return {"unit": unit.id, "dropped": dropped}


def prune_units(units: list[Unit]) -> dict[str, Any]:
    report = {"units": 0, "dropped": 0}
    for unit in units:
        info = prune_unit(unit)
        if info["dropped"]:
            report["units"] += 1
            report["dropped"] += len(info["dropped"])
    return report
