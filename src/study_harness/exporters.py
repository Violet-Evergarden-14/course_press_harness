"""Output formats (docs/rendering.md §10).

One document model, several writers. `_do_render` builds the resolved content once and
each exporter turns it into a file. Formats fall into two groups, and the difference
matters:

* **book-shaped** (md, tex, pdf) — the intended output: continuous, numbered, complete.
* **derived** (txt, docx, pptx) — convenience exports. pptx especially is lossy: a book
  is not a deck. Full text goes into the speaker notes so nothing is silently dropped.

No exporter invents content; they only re-lay-out what the renderer already produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .knowledge import Unit
from .render import display_title, number_units

MARKUP_RE = re.compile(r"\*\*(.+?)\*\*")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
EQUATION_RE = re.compile(r"^\$\$(.*)\$\$$", re.S)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


# --------------------------------------------------------------------------
# plain text
# --------------------------------------------------------------------------

def to_text(markdown: str) -> str:
    """Flatten a rendered document to plain text without losing structure words."""
    out: list[str] = []
    for line in markdown.splitlines():
        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = MARKUP_RE.sub(r"\1", m.group(2)).strip()
            if level == 1:
                out += ["", "=" * max(8, len(title) * 2), title, "=" * max(8, len(title) * 2), ""]
            elif level == 2:
                out += ["", f"【{title}】", ""]
            else:
                out += ["", f"{title}", "-" * max(4, len(title))]
            continue
        line = IMAGE_RE.sub(lambda mm: f"[图：{mm.group(1)}]", line)
        line = WIKILINK_RE.sub(lambda mm: mm.group(2) or mm.group(1), line)
        line = MARKUP_RE.sub(r"\1", line)
        line = EQUATION_RE.sub(lambda mm: f"    {mm.group(1).strip()}", line)
        line = line.replace("$$", "")
        out.append(line.rstrip())
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    return text


# --------------------------------------------------------------------------
# Office formats
# --------------------------------------------------------------------------

@dataclass
class DocItem:
    """A resolved piece of the document: heading, paragraph, table or image."""
    kind: str           # chapter | section | unit | label | paragraph | table | image
    text: str = ""
    rows: list[list[str]] | None = None
    asset: str | None = None


def document_items(units: list[Unit], outline: dict[str, Any], *,
                   profile: str, templates) -> list[DocItem]:
    """Flatten the outline into a linear list of presentational items."""
    numbers = number_units(outline)
    by_id = {u.id: u for u in units}
    items: list[DocItem] = []
    order_names = templates.order(profile) if templates is not None else []

    for part in outline.get("parts", []):
        kind = part.get("kind")
        if kind == "front_matter":
            continue
        label = part.get("label") or str(part.get("no"))
        heading = f"第 {label} 讲 {part.get('title', '')}" if kind == "lecture" else part.get("title", "")
        items.append(DocItem("chapter", heading))
        for sec in part.get("sections", []):
            sec_title = display_title(sec.get("title", ""))
            if sec_title:
                items.append(DocItem("section", sec_title))
            for uid in sec.get("units", []):
                unit = by_id.get(uid)
                if unit is None:
                    continue
                utitle = display_title(unit.title)
                if utitle and utitle != sec_title:
                    items.append(DocItem("unit", utitle))
                names = [n for n in order_names if n in unit.sections] + \
                        [n for n in unit.sections if n not in order_names]
                for name in names:
                    lines = [ln.strip() for ln in unit.sections.get(name) or [] if ln.strip()]
                    if not lines:
                        continue
                    items.append(DocItem("label", name))
                    for line in lines:
                        if name == "表格" and line.startswith("|"):
                            rows = _parse_md_table(line)
                            if rows:
                                items.append(DocItem("table", rows=rows))
                            continue
                        m = EQUATION_RE.match(line)
                        if m:
                            items.append(DocItem("paragraph", m.group(1).strip()))
                            continue
                        for asset in _assets_in(line):
                            items.append(DocItem("image", asset=asset))
                        items.append(DocItem("paragraph", _plain(line)))
    return items


def _parse_md_table(text: str) -> list[list[str]]:
    rows = []
    for raw in text.splitlines():
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if cells and not set("".join(cells)) <= set("- "):
            rows.append(cells)
    return rows if len(rows) >= 2 else []


def _assets_in(line: str) -> list[str]:
    return [m.group(2) for m in IMAGE_RE.finditer(line) if not m.group(2).startswith("http")]


def _plain(line: str) -> str:
    line = IMAGE_RE.sub("", line)
    line = WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), line)
    line = MARKUP_RE.sub(r"\1", line)
    return line.strip()


def to_docx(items: list[DocItem], path: Path, *, title: str,
            assets_dir: Path | None = None) -> Path:
    from docx import Document
    from docx.shared import Inches

    doc = Document()
    doc.add_heading(title, level=0)
    for item in items:
        if item.kind == "chapter":
            doc.add_heading(item.text, level=1)
        elif item.kind == "section":
            doc.add_heading(item.text, level=2)
        elif item.kind == "unit":
            doc.add_heading(item.text, level=3)
        elif item.kind == "label":
            p = doc.add_paragraph()
            p.add_run(item.text).bold = True
        elif item.kind == "table" and item.rows:
            rows = item.rows
            table = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
            table.style = "Table Grid"
            for r, row in enumerate(rows):
                for c, cell in enumerate(row):
                    table.cell(r, c).text = cell
        elif item.kind == "image" and item.asset and assets_dir is not None:
            candidate = assets_dir / Path(item.asset).name
            if candidate.exists():
                try:
                    doc.add_picture(str(candidate), width=Inches(4.5))
                except Exception:
                    doc.add_paragraph(f"[图：{candidate.name}]")
        elif item.kind == "paragraph" and item.text:
            doc.add_paragraph(item.text)
    doc.save(str(path))
    return path


def to_pptx(items: list[DocItem], path: Path, *, title: str,
            assets_dir: Path | None = None, max_bullets: int = 6) -> Path:
    """Lossy by nature: one slide per unit, full text preserved as speaker notes."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    title_slide.shapes.title.text = title
    if len(title_slide.placeholders) > 1:
        title_slide.placeholders[1].text = "由 study-harness 生成"

    current: dict[str, Any] | None = None   # pending unit slide
    pending: list[str] = []

    def flush() -> None:
        nonlocal current, pending
        if current is None:
            return
        slide = current["slide"]
        body = slide.placeholders[1].text_frame
        body.text = pending[0] if pending else ""
        for line in pending[1:max_bullets]:
            body.add_paragraph().text = line
        # nothing is dropped: the remainder goes into the speaker notes
        slide.notes_slide.notes_text_frame.text = "\n".join(pending)
        current = None
        pending = []

    for item in items:
        if item.kind in {"chapter", "section"}:
            flush()
            slide = prs.slides.add_slide(prs.slide_layouts[2])
            slide.shapes.title.text = item.text
            continue
        if item.kind == "unit":
            flush()
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = item.text
            current = {"slide": slide}
            continue
        if current is None:
            continue
        if item.kind == "paragraph" and item.text:
            pending.append(item.text)
        elif item.kind == "table" and item.rows:
            pending.extend(" | ".join(row) for row in item.rows)
        elif item.kind == "image" and item.asset and assets_dir is not None:
            candidate = assets_dir / Path(item.asset).name
            if candidate.exists():
                try:
                    current["slide"].shapes.add_picture(str(candidate), Inches(5), Inches(4.5),
                                                        height=Inches(2.2))
                except Exception:
                    pending.append(f"[图：{candidate.name}]")
    flush()

    prs.save(str(path))
    return path


