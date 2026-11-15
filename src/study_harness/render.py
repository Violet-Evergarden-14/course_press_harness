"""Deterministic rendering (docs/rendering.md).

No model calls. No timestamps in the body. Same inputs -> byte-identical output.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .knowledge import Unit
from .templates import TemplateSet

REF_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

# docs/architecture.md §6.3 — forbidden in `none` and `footnote` modes.
# Two tiers: a bare term (校名、课程名、"PPT原例") is not the same as a pointer to a
# location ("见课件第 12 页"). The first is a wording fix, the second breaks the
# promise that the book stands on its own.
STRONG_SOURCE_PATTERNS = [
    r"(?:见|参见|详见|根据|依据|摘自|如)\s*(?:课件|幻灯片|PPT|ppt|讲义原文|原书)",
    r"(?:课件|幻灯片|PPT|ppt)\s*(?:第\s*\d+\s*页|原例|原文|中|里|上|所示)",
    r"(?:老师|课上|课堂|录音|视频)\s*(?:中|里|上|提到|说|讲|强调|所示)",
    r"第\s*\d+\s*页\s*(?:课件|幻灯片|PPT|录音|视频)",
]
SOFT_SOURCE_WORDS = [
    "课件", "PPT", "ppt", "幻灯片", "该页", "老师", "课上", "课堂", "录音", "视频", "原书", "讲义原文",
]

# section order per profile (data, not branching in code — see docs/rendering.md §4)
SECTION_ORDER = {
    "learn": ["定义", "陈述", "说明", "问题", "步骤", "复杂度", "依据", "用途", "算式", "表格",
              "图示", "例子", "示例", "验证", "易错", "错误说法", "正确说法", "原因", "要点", "备注"],
    "review": ["陈述", "定义", "要点", "易错", "错误说法", "正确说法", "原因", "用途", "依据",
               "说明", "问题", "步骤", "复杂度", "算式", "表格", "图示", "例子", "示例", "验证", "备注"],
}


@dataclass
class Rendered:
    text: str
    audit_text: str
    assets: list[Path]
    numbering: dict[str, str]


def _ordered_sections(unit: Unit, profile: str,
                      templates: "TemplateSet | None" = None) -> list[tuple[str, list[str]]]:
    order = templates.order(profile) if templates is not None else SECTION_ORDER.get(profile, SECTION_ORDER["learn"])
    known = [(name, unit.sections[name]) for name in order if name in unit.sections]
    extra = [(name, lines) for name, lines in unit.sections.items()
             if name not in order]
    return known + extra


TITLE_NUM_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)+\.?|\d+\.)\s*")


def display_title(title: str) -> str:
    """Drop a leading section number that the book already supplies.

    Keeping both yields headings like "6.2.1 6.1 树的定义".
    """
    stripped = TITLE_NUM_RE.sub("", title or "").strip()
    return stripped or (title or "")


def number_units(outline: dict[str, Any]) -> dict[str, str]:
    numbers: dict[str, str] = {}
    part_no = 0
    for part in outline.get("parts", []):
        if part.get("kind") == "front_matter":
            continue
        part_no += 1
        label = part.get("label") or str(part.get("no") or part_no)
        for s_idx, sec in enumerate(part.get("sections", []), start=1):
            for u_idx, uid in enumerate(sec.get("units", []), start=1):
                numbers[uid] = f"{label}.{s_idx}.{u_idx}"
    return numbers


def render_document(
    units: list[Unit],
    outline: dict[str, Any],
    *,
    profile: str = "learn",
    title: str = "讲义",
    assets_src: Path | None = None,
    assets_dst: Path | None = None,
    notes: list[dict[str, Any]] | None = None,
    glossary: dict[str, Any] | None = None,
    figures: dict[str, Any] | None = None,
    templates: "TemplateSet | None" = None,
) -> Rendered:
    templates = templates or TemplateSet()
    by_id = {u.id: u for u in units}
    numbers = number_units(outline)
    notes = notes or []
    glossary = glossary or {}
    figures = figures or {}
    term_names = {t.get("id"): t.get("zh") or t.get("en") or t.get("id")
                  for t in glossary.get("terms", [])}

    used_assets: list[Path] = []

    def resolve(text: str, unit_id: str | None = None) -> str:
        def repl(m: re.Match) -> str:
            target, alias = m.group(1).strip(), m.group(2)
            if target.startswith("term:"):
                return term_names.get(target[5:], target[5:])
            if target.startswith("fig:"):
                fig = figures.get(target[4:])
                if fig is not None:
                    if fig.binary():
                        used_assets.append(fig.path)
                    return fig.emit()
                return ""
            if target.startswith("asset:"):
                name = target[6:]
                for ext in (".png", ".jpg", ".jpeg", ".gif", ".emf", ".svg"):
                    src = (assets_src or Path(".")) / f"{name}{ext}"
                    if src.exists():
                        used_assets.append(src)
                        rel = f"book.assets/{src.name}"
                        return f"![{name}]({rel})"
                return f"![{name}](book.assets/{name})"
            shown = numbers.get(target)
            if shown is None:
                # outside current scope or missing: degrade to title, never a dangling §
                other = by_id.get(target)
                return alias or (f"《{other.title}》" if other else target)
            return alias or f"§{shown}"

        return REF_RE.sub(repl, text)

    out: list[str] = [templates.fmt("document", "title", title=title), ""]
    toc: list[str] = [templates.fmt("document", "toc_heading"), ""]

    for part in outline.get("parts", []):
        kind = part.get("kind")
        ptitle = part.get("title", "")
        if kind == "front_matter":
            toc.append(f"- {ptitle}")
            continue
        label = part.get("label") or str(part.get("no"))
        if kind == "lecture":
            toc.append(templates.fmt("document", "toc_part", label=label, title=ptitle))
        else:
            toc.append(templates.fmt("document", "toc_part_other", title=ptitle))
        out.append(templates.fmt("part", kind if kind == "lecture" else "other",
                                 label=label, title=ptitle))
        out.append("")
        for s_idx, sec in enumerate(part.get("sections", []), start=1):
            sec_title = display_title(sec.get("title", ""))
            out.append(templates.fmt("section", "heading", label=label, index=s_idx, title=sec_title))
            out.append("")
            toc.append(templates.fmt("document", "toc_section", label=label, index=s_idx, title=sec_title))
            for uid in sec.get("units", []):
                unit = by_id.get(uid)
                if unit is None:
                    continue
                num = numbers.get(uid, "?")
                utitle = display_title(unit.title)
                show_heading = utitle and not (
                    templates.omit_duplicate_heading() and utitle == sec_title)
                if show_heading:
                    out.append(templates.fmt("unit", "heading", number=num, title=utitle))
                    out.append("")
                    toc.append(templates.fmt("document", "toc_unit", number=num, title=utitle))
                for name, lines in _ordered_sections(unit, profile, templates):
                    body = _apply_notes(unit, name, lines, notes, profile)
                    if not body:
                        continue
                    out.append(templates.fmt("unit", "section_label", name=name))
                    out.append("")
                    out.extend(resolve(line, uid) for line in body)
                    out.append("")

    text = "\n".join(out).rstrip() + "\n"
    toc_text = "\n".join(toc) + "\n"
    text = _insert_toc(text, toc_text)

    audit = _audit_version(units, by_id, numbers)

    if assets_dst is not None:
        assets_dst.mkdir(parents=True, exist_ok=True)
        for src in used_assets:
            if src.exists():
                shutil.copyfile(src, assets_dst / src.name)

    return Rendered(text=text, audit_text=audit, assets=used_assets, numbering=numbers)


def _insert_toc(text: str, toc: str) -> str:
    marker = "## 目录"
    if marker in text:
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join([lines[0], "", toc.rstrip(), "", *lines[1:]]).rstrip() + "\n"
    return toc + "\n" + text


def _apply_notes(unit: Unit, section: str, lines: list[str],
                 notes: list[dict[str, Any]], profile: str) -> list[str]:
    """Overlay student notes: correction replaces a section, emphasis appends a marker."""
    result = list(lines)
    for n in notes:
        at = n.get("loc") or {}
        if at.get("unit") != unit.id:
            continue
        kind = n.get("kind")
        if kind == "correction" and n.get("section") == section:
            result = [n.get("text", "")]
        elif kind == "emphasis" and profile == "review":
            result = result + [f"> **重点**：{n.get('text', '')}"]
    return result


def _audit_version(units: list[Unit], by_id: dict[str, Unit], numbers: dict[str, str]) -> str:
    """Locator-bearing version for human checking; never ships (docs/rendering.md §7.2)."""
    lines = ["# 定位核对表", ""]
    for u in units:
        lines.append(f"## {numbers.get(u.id, '?')} {u.title}")
        for e in u.evidence:
            lines.append(f"- {e.get('src')} {e.get('loc')} block={e.get('block', '-')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


import re as _re

_STRONG_RE = [_re.compile(p) for p in STRONG_SOURCE_PATTERNS]


def forbidden_hits(text: str) -> list[tuple[int, str, str, str]]:
    """(line, severity, matched, snippet) for source references in the body."""
    hits: list[tuple[int, str, str, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        match = next((m for r in _STRONG_RE for m in [r.search(line)] if m), None)
        if match:
            hits.append((i, "blocker", match.group(0), line.strip()[:80]))
            continue
        for w in SOFT_SOURCE_WORDS:
            if w in line:
                hits.append((i, "minor", w, line.strip()[:80]))
                break
    return hits


def load_notes(workspace: Path) -> list[dict[str, Any]]:
    p = workspace / ".study" / "patches" / "notes.yaml"
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    return data if isinstance(data, list) else data.get("notes", [])


def load_glossary(workspace: Path) -> dict[str, Any]:
    for p in (workspace / ".study" / "glossary.yaml", workspace / "glossary.yaml"):
        if p.exists():
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {"terms": []}
