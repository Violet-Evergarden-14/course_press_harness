"""PowerPoint adapter (docs/ingest.md §4).

Deterministic: no model calls. OMML math is linearized to LaTeX where possible;
unsupported constructs degrade to text and are recorded as an L2 gap candidate.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterator

from lxml import etree

from ..ids import src_key
from ..ir import Block
from . import register
from .base import Adapter, IngestResult

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"

HEADING_FONT_PT = 24.0
LIST_ITEM_RE = re.compile(r"^\s*(?:\d+|[一二三四五六七八九十]+)[.、)]\s*")
CJK_RE = re.compile(r"[一-鿿]")
NOISE_HEADING_RE = re.compile(r"[\s.·…\-—_()（）\[\]【】0-9]+")
CHAPTER_PATTERN = re.compile(r"^\s*第\s*\d+\s*[章节]")

TIMECODE_RE = re.compile(r"(?<!\d)(\d{1,2}):([0-5]\d)(?::([0-5]\d))?(?!\d)")


def _q(tag: str) -> str:
    return f"{{{M_NS}}}{tag}"


def _localname(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


# --------------------------------------------------------------------------
# OMML -> LaTeX (deterministic subset)
# --------------------------------------------------------------------------

def _omml_children(el) -> list:
    return [c for c in el if isinstance(c.tag, str)]


def _omml_to_latex(el) -> str:
    if not isinstance(el.tag, str):
        return ""
    tag = _localname(el)
    kids = _omml_children(el)

    def child(name: str):
        for c in kids:
            if _localname(c) == name:
                return c
        return None

    def convert_all(node) -> str:
        return "".join(_omml_to_latex(c) for c in _omml_children(node))

    if tag in {"oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "fName", "lim"}:
        return convert_all(el)
    if tag == "t":
        return el.text or ""
    if tag == "r":
        return convert_all(el)
    if tag == "f":  # fraction
        num = child("num")
        den = child("den")
        return f"\\frac{{{convert_all(num) if num is not None else ''}}}{{{convert_all(den) if den is not None else ''}}}"
    if tag == "sSup":
        base = child("e")
        sup = child("sup")
        return f"{convert_all(base) if base is not None else ''}^{{{convert_all(sup) if sup is not None else ''}}}"
    if tag == "sSub":
        base = child("e")
        sub = child("sub")
        return f"{convert_all(base) if base is not None else ''}_{{{convert_all(sub) if sub is not None else ''}}}"
    if tag == "sSubSup":
        base, sub, sup = child("e"), child("sub"), child("sup")
        b = convert_all(base) if base is not None else ""
        return f"{b}_{{{convert_all(sub) if sub is not None else ''}}}^{{{convert_all(sup) if sup is not None else ''}}}"
    if tag == "rad":  # radical
        deg = child("deg")
        e = child("e")
        body = convert_all(e) if e is not None else ""
        degs = convert_all(deg) if deg is not None else ""
        return f"\\sqrt[{degs}]{{{body}}}" if degs.strip() else f"\\sqrt{{{body}}}"
    if tag == "d":  # delimiter
        return convert_all(el)
    if tag == "nary":  # sum / integral
        chr_el = child("naryPr")
        op = "\\sum"
        if chr_el is not None:
            val = chr_el.find(_q("chr"))
            if val is not None:
                c = val.get(_q("val")) or val.get("val") or ""
                op = {"∑": "\\sum", "∏": "\\prod", "∫": "\\int", "∮": "\\oint"}.get(c, "\\sum")
        sub, sup, e = child("sub"), child("sup"), child("e")
        out = op
        if sub is not None:
            out += f"_{{{convert_all(sub)}}}"
        if sup is not None:
            out += f"^{{{convert_all(sup)}}}"
        return out + (convert_all(e) if e is not None else "")
    # unknown construct: keep its text content, mark nothing (caller flags it)
    return convert_all(el)


def _is_unknown_omml(el) -> bool:
    known = {
        "oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "fName", "lim", "t", "r",
        "f", "sSup", "sSub", "sSubSup", "rad", "d", "nary", "naryPr", "chr", "ctrlPr", "rPr", "dPr",
        "sSupPr", "sSubPr", "fPr", "radPr", "argPr", "func", "fname", "box", "borderBox", "groupChr",
        "m", "mr", "eqArr", "bar", "acc", "limLow", "limUpp", "sPre", "phant",
    }
    return _localname(el) not in known


# --------------------------------------------------------------------------
# adapter
# --------------------------------------------------------------------------

@register
class PptxAdapter(Adapter):
    name = "pptx"
    accepts = (".pptx", ".pptm")

    def probe(self, path: Path) -> dict[str, Any]:
        from pptx import Presentation

        prs = Presentation(str(path))
        notes = 0
        timecoded = 0
        for slide in prs.slides:
            if slide.has_notes_slide:
                txt = slide.notes_slide.notes_text_frame.text or ""
                if txt.strip():
                    notes += 1
                if TIMECODE_RE.search(txt):
                    timecoded += 1
        return {
            "present": True,
            "size": path.stat().st_size,
            "slides": len(prs.slides),
            "speaker_notes": notes,
            "notes_with_timecodes": timecoded,
        }

    def ingest(self, path: Path, ctx: Any) -> IngestResult:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        res = IngestResult()
        prs = Presentation(str(path))
        key = src_key(path.name, self.name)
        assets_dir: Path = ctx.assets_dir

        running = self._running_headers(prs)
        for s_idx, slide in enumerate(prs.slides, start=1):
            if str(slide.element.get("show", "1")) == "0":
                res.warnings.append({"stage": "pptx/hidden", "severity": "warning",
                                     "msg": "隐藏幻灯片已跳过", "slide": s_idx})
                continue

            counter = {"n": 0}

            def next_id() -> str:
                counter["n"] += 1
                return f"{key}-{s_idx:03d}-{counter['n']}"

            blocks: list[Block] = []
            self._walk(slide.shapes, s_idx, next_id, blocks, res, assets_dir, path)

            # speaker notes attach to the last block of the slide
            if slide.has_notes_slide:
                notes = (slide.notes_slide.notes_text_frame.text or "").strip()
                if notes:
                    if blocks:
                        blocks[-1].speaker_notes = notes
                    else:
                        blocks.append(Block(id=next_id(), src=path.name, type="bullet",
                                            loc={"slide": s_idx}, text=notes,
                                            role="speaker_notes"))

            self._promote_heading(blocks, running)
            ordered = self._order_blocks(blocks)
            res.blocks.extend(ordered)

        res.capabilities = self.probe(path)
        return res

    # -- helpers ---------------------------------------------------------
    def _order_blocks(self, blocks: list[Block]) -> list[Block]:
        """Titles first, then document order (docs/ingest.md §4.1)."""
        titles = [b for b in blocks if b.type == "title"]
        rest = [b for b in blocks if b.type != "title"]
        return titles + rest

    def _walk(self, shapes, s_idx: int, next_id, out: list[Block], res: IngestResult,
              assets_dir: Path, path: Path) -> None:
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                self._walk(shape.shapes, s_idx, next_id, out, res, assets_dir, path)
                continue
            try:
                self._shape(shape, s_idx, next_id, out, res, assets_dir, path)
            except Exception as exc:  # never let one shape kill the file
                res.warnings.append({"stage": "pptx/shape", "severity": "warning",
                                     "msg": f"形状解析失败: {exc}", "slide": s_idx})

    def _shape(self, shape, s_idx, next_id, out, res, assets_dir, path) -> None:
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        # formulas first: their text is duplicated in the shape's text frame
        formulas = self._formulas(shape, s_idx, next_id, res, path.name)
        if formulas:
            out.extend(formulas)

        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            out.append(self._picture(shape, s_idx, next_id, assets_dir, path.name))
            return

        if getattr(shape, "has_table", False) and shape.has_table:
            rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
            rows = [r for r in rows if any(c for c in r)]
            if rows:
                out.append(Block(id=next_id(), src=path.name, type="table",
                                 loc={"slide": s_idx, "shape": shape.shape_id}, rows=rows))
            return

        if not getattr(shape, "has_text_frame", False) or not shape.has_text_frame:
            if shape.shape_type == MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT:
                res.warnings.append({"stage": "pptx/ole", "severity": "warning",
                                     "msg": "内嵌 OLE 对象未处理", "slide": s_idx})
            return

        if formulas:
            return  # text already represented as formulas

        is_title = False
        level = 0
        try:
            if shape.is_placeholder and shape.placeholder_format.type is not None:
                ph = shape.placeholder_format
                is_title = ph.type in (13, 1, 3)  # TITLE, TITLE?, CENTER_TITLE
                if ph.idx == 0:
                    level = 1
        except Exception:
            pass

        font = self._max_font(shape)
        top = int(shape.top or 0)
        for para in shape.text_frame.paragraphs:
            text = "".join(run.text for run in para.runs).strip() or para.text.strip()
            if not text:
                continue
            if is_title:
                out.append(Block(id=next_id(), src=path.name, type="title", level=1,
                                 loc={"slide": s_idx, "shape": shape.shape_id}, text=text))
            else:
                out.append(Block(id=next_id(), src=path.name, type="bullet",
                                 level=para.level + 1,
                                 loc={"slide": s_idx, "shape": shape.shape_id}, text=text,
                                 extra={"font": font, "top": top} if font else {"top": top}))

    def _max_font(self, shape) -> float:
        """Largest explicit font size in a text shape, 0 when inherited."""
        sizes: list[float] = []
        try:
            for para in shape.text_frame.paragraphs:
                if para.font is not None and para.font.size is not None:
                    sizes.append(para.font.size.pt)
                for run in para.runs:
                    if run.font is not None and run.font.size is not None:
                        sizes.append(run.font.size.pt)
        except Exception:
            return 0.0
        return max(sizes) if sizes else 0.0

    def _running_headers(self, prs) -> set[str]:
        """Large text repeated on many slides is a running header, not a heading."""
        from collections import Counter

        counts: Counter[str] = Counter()
        for slide in prs.slides:
            seen = set()
            for shape in slide.shapes:
                if not getattr(shape, 'has_text_frame', False) or not shape.has_text_frame:
                    continue
                if self._max_font(shape) < HEADING_FONT_PT:
                    continue
                text = (shape.text_frame.text or '').strip()
                if text and len(text) <= 60:
                    seen.add(text)
            counts.update(seen)
        # half the deck, and never fewer than 3: a section heading legitimately
        # repeats on its own slides, a running header repeats on most of them
        threshold = max(3, -(-len(prs.slides) // 2))
        return {t for t, c in counts.items() if c >= threshold}

    def _promote_heading(self, blocks: list[Block], running: set[str] | None = None) -> None:
        """Infer a slide heading when the deck uses plain text boxes for it.

        Many decks have no title placeholder at all: the heading is just the largest
        text on the slide. Without this, every slide's heading becomes a bullet and the
        whole lecture collapses into a single unit.
        """
        if any(b.type == "title" for b in blocks):
            return
        running = running or set()
        candidates = []
        for b in blocks:
            if b.type != "bullet" or not b.text or len(b.text) > 60:
                continue
            if float((b.extra or {}).get("font") or 0) < HEADING_FONT_PT:
                continue
            text = b.text.strip()
            if text in running or len(NOISE_HEADING_RE.sub("", text)) < 2:
                continue
            candidates.append(b)

        if not candidates:
            # No large text: fall back to the best short line by (font, CJK presence).
            # Purely topmost is not enough — on code slides that picks "{" or "p".
            scored = []
            for b in blocks:
                if b.type != "bullet" or not b.text:
                    continue
                text = b.text.strip()
                if not text or len(text) > 30 or text in running:
                    continue
                if CHAPTER_PATTERN.match(text) or text.endswith(("。", "，", "；", "：", ";", ",")):
                    continue
                if CJK_RE.search(text) is None and len(text) <= 8:
                    continue  # identifier / code fragment, not a heading
                if LIST_ITEM_RE.match(text):
                    continue  # "1. 依次取出 ..." is a list item, not a heading
                scored.append((float((b.extra or {}).get("font") or 0),
                              1 if CJK_RE.search(text) else 0,
                              -int((b.extra or {}).get("top") or 0), b))
            if scored:
                candidates = [max(scored, key=lambda x: x[:3])[3]]
        if not candidates:
            return
        best = max(candidates, key=lambda b: (float((b.extra or {}).get("font") or 0),
                                              -int((b.extra or {}).get("top") or 0)))
        best.type = "title"
        best.level = 1 if CHAPTER_PATTERN.match(best.text or "") else 2

    def _formulas(self, shape, s_idx, next_id, res, src_name: str) -> list[Block]:
        blocks: list[Block] = []
        try:
            xml = shape._element
        except Exception:
            return blocks
        for om in xml.iter(_q("oMathPara"), _q("oMath")):
            if _localname(om) != "oMath":
                continue
            latex = _omml_to_latex(om).strip()
            if not latex:
                continue
            unknown = [k for k in om.iter() if isinstance(k.tag, str) and _is_unknown_omml(k)]
            if unknown:
                res.warnings.append({"stage": "pptx/omml", "severity": "warning",
                                     "msg": "公式含不支持结构，已降级为文本", "slide": s_idx})
            blocks.append(Block(id=next_id(), src=src_name, type="formula",
                                loc={"slide": s_idx, "shape": shape.shape_id}, latex=latex))
        return blocks

    def _picture(self, shape, s_idx, next_id, assets_dir: Path, src_name: str) -> Block:
        blob = shape.image.blob
        ext = shape.image.ext
        digest = hashlib.sha256(blob).hexdigest()[:8]
        name = f"{digest}.{ext}"
        assets_dir.mkdir(parents=True, exist_ok=True)
        target = assets_dir / name
        if not target.exists():
            target.write_bytes(blob)
        return Block(id=next_id(), src=src_name, type="image", asset=f"assets/{name}",
                     loc={"slide": s_idx, "shape": shape.shape_id})
