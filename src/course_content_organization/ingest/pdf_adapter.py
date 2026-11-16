"""PDF adapter (docs/ingest.md §5).

Uses pdfplumber for coordinates. Two things matter here and neither is optional:

* column order — naive extraction interleaves two-column text into nonsense;
* running headers/footers — they would become body text on every page.

Scanned pages are detected and reported, not silently skipped.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..ids import src_key
from ..ir import Block
from . import register
from .base import Adapter, IngestResult

SCAN_CHAR_THRESHOLD = 50     # median chars per page below this -> scanned
HEADER_RATIO = 0.4           # a line repeated on this share of pages is furniture
COLUMN_GAP_RATIO = 0.18      # x-gap / page width that separates two columns

# structural headings, recognized regardless of font size: "第 3 章 示例标题", "0.1 示例小节"
CHAPTER_RE = re.compile(r"^\s*第\s*\d+\s*[章节]")
STRUCT_RE = re.compile(
    r"^\s*(?:第\s*\d+\s*[章节]|\d+(?:\.\d+)+(?=\s|[^\x00-\x7f])|\d+\.\s)")
# table-of-contents pages: dot leaders or many chapter refs
TOC_LEADER_RE = re.compile(r"(?:\.{4,}|…{2,}|·{4,}|…)")
TOC_REF_RE = re.compile(r"第\s*\d+\s*[章节]")


def _median(values: list[int]) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[len(s) // 2]


@register
class PdfAdapter(Adapter):
    name = "pdf"
    accepts = (".pdf",)

    def _open(self, path: Path):
        import pdfplumber

        return pdfplumber.open(str(path))

    def probe(self, path: Path) -> dict[str, Any]:
        try:
            with self._open(path) as pdf:
                counts = []
                images = 0
                for page in pdf.pages[:20]:
                    counts.append(len((page.extract_text() or "").strip()))
                    images += len(page.images)
                total = len(pdf.pages)
        except Exception as exc:  # noqa: BLE001
            return {"present": True, "valid": False, "error": str(exc)}
        return {
            "present": True,
            "size": path.stat().st_size,
            "pages": total,
            "chars_median": _median(counts),
            "images_sampled": images,
            "kind": "scanned" if _median(counts) < SCAN_CHAR_THRESHOLD else "text",
        }

    def ingest(self, path: Path, ctx: Any) -> IngestResult:
        res = IngestResult()
        key = src_key(path.name, self.name)
        assets_dir: Path = ctx.assets_dir

        try:
            pdf = self._open(path)
        except Exception as exc:  # noqa: BLE001
            res.warnings.append({"stage": "pdf/open", "severity": "error", "msg": str(exc)})
            return res

        with pdf:
            pages = list(pdf.pages)
            page_lines = [self._lines(page) for page in pages]
            furniture = self._furniture(page_lines)

            for pno, (page, lines) in enumerate(zip(pages, page_lines), start=1):
                if len("".join(w["text"] for line in lines for w in line["words"])) < SCAN_CHAR_THRESHOLD:
                    if page.images:
                        res.warnings.append({
                            "stage": "pdf/scan", "severity": "warning", "page": pno,
                            "msg": "页面无可提取文字层，含图片，需 OCR 或多模态处理"})
                        # register the whole page as an asset-less figure request candidate
                        res.blocks.append(Block(
                            id=f"{key}-p{pno:03d}-scan", src=path.name, type="image",
                            loc={"page": pno}, asset=None,
                            semantics=None, kind="scanned_page"))
                    continue

                kept = [ln for ln in lines if not self._is_furniture(ln, furniture, pno)
                        and ln["text"].strip()]
                if self._is_toc_page(kept):
                    res.warnings.append({"stage": "pdf/toc", "severity": "warning",
                                         "page": pno, "msg": "目录页已跳过"})
                    continue
                for n, block in enumerate(self._to_blocks(kept, pno, key, path.name), start=1):
                    res.blocks.append(block)

        res.capabilities = self.probe(path)
        if res.capabilities.get("kind") == "scanned":
            res.warnings.append({"stage": "pdf/scan", "severity": "warning",
                                 "msg": "整份文件疑似扫描件，文字提取可能不完整"})
        return res

    # -- helpers ---------------------------------------------------------
    def _to_blocks(self, lines: list[dict[str, Any]], pno: int, key: str, src: str) -> list[Block]:
        """Turn lines into blocks, merging wrapped paragraph lines.

        Body text wraps at the column edge; emitting each visual line as its own block
        would shred every paragraph into fragments.
        """
        blocks: list[Block] = []
        if not lines:
            return blocks
        body = lines[0]["body_size"]
        n = 0
        i = 0
        while i < len(lines):
            line = lines[i]
            size = line["size"]
            structural = bool(STRUCT_RE.match(line["text"].strip()))
            is_heading = structural or size >= body * 1.15
            if is_heading:
                n += 1
                level = 1 if (size >= body * 1.4 or CHAPTER_RE.match(line["text"].strip())) else 2
                blocks.append(Block(
                    id=f"{key}-p{pno:03d}-{n}", src=src, type="title", level=level,
                    loc={"page": pno, "bbox": [round(v, 1) for v in line["bbox"]]},
                    text=line["text"].strip()))
                i += 1
                continue

            # paragraph: absorb following lines of the same size at normal spacing
            parts = [line["text"].strip()]
            prev = line
            j = i + 1
            line_height = max(1.0, line["bbox"][3] - line["bbox"][1])
            while j < len(lines):
                nxt = lines[j]
                if nxt["size"] >= body * 1.15:
                    break
                if abs(nxt["size"] - size) > 0.6:
                    break
                gap = nxt["top"] - prev["bbox"][3]
                if gap > line_height * 0.9:
                    break
                parts.append(nxt["text"].strip())
                prev = nxt
                j += 1
            n += 1
            blocks.append(Block(
                id=f"{key}-p{pno:03d}-{n}", src=src, type="bullet", level=1,
                loc={"page": pno, "bbox": [round(v, 1) for v in line["bbox"]]},
                text="".join(parts)))
            i = j
        return blocks

    def _is_toc_page(self, lines: list[dict[str, Any]]) -> bool:
        """A contents page: dot leaders, or many chapter refs on short lines.

        Left in place, a TOC turns into one paragraph of concatenated chapter titles
        that then competes with the real headings downstream.
        """
        if len(lines) < 4:
            return False
        leaders = sum(1 for ln in lines if TOC_LEADER_RE.search(ln["text"]))
        refs = sum(1 for ln in lines if TOC_REF_RE.match(ln["text"].strip()))
        return leaders >= max(3, len(lines) // 4) or refs >= 5

    def _lines(self, page) -> list[dict[str, Any]]:
        words = page.extract_words(extra_attrs=["size"], use_text_flow=False, keep_blank_chars=False)
        if not words:
            return []
        sizes = Counter(round(w.get("size") or 0, 1) for w in words)
        body_size = sizes.most_common(1)[0][0]
        page_max = max(round(w.get("size") or 0, 1) for w in words)

        # group words into lines by vertical position
        rows: dict[int, list[dict]] = {}
        for w in words:
            rows.setdefault(int(round(w["top"] / 3)), []).append(w)

        lines: list[dict[str, Any]] = []
        for _, ws in sorted(rows.items()):
            ws.sort(key=lambda w: w["x0"])
            text = "".join(w["text"] for w in ws)
            lines.append({
                "words": ws,
                "text": text,
                "top": min(w["top"] for w in ws),
                "x0": min(w["x0"] for w in ws),
                "bbox": [min(w["x0"] for w in ws), min(w["top"] for w in ws),
                         max(w["x1"] for w in ws), max(w["bottom"] for w in ws)],
                "size": max(round(w.get("size") or 0, 1) for w in ws),
                "page_max_size": page_max,
                "body_size": body_size,
            })
        return self._order_columns(lines, page.width)

    def _order_columns(self, lines: list[dict[str, Any]], page_width: float) -> list[dict[str, Any]]:
        """Read column by column when the page is two-column.

        A line is assigned to the left or right column by its horizontal centre; the
        gap must be wide relative to the page, otherwise single-column reading order
        (top to bottom) is kept.
        """
        if len(lines) < 6 or page_width <= 0:
            return lines

        mids = sorted((ln["bbox"][2] + ln["bbox"][0]) / 2 for ln in lines)
        gaps = [(mids[i + 1] - mids[i], i) for i in range(len(mids) - 1)]
        biggest, idx = max(gaps) if gaps else (0, 0)
        if biggest / page_width < COLUMN_GAP_RATIO:
            return lines

        cut = mids[idx] + biggest / 2
        left = [ln for ln in lines if (ln["bbox"][0] + ln["bbox"][2]) / 2 < cut]
        right = [ln for ln in lines if (ln["bbox"][0] + ln["bbox"][2]) / 2 >= cut]
        if not left or not right:
            return lines
        return sorted(left, key=lambda ln: ln["top"]) + sorted(right, key=lambda ln: ln["top"])

    def _furniture(self, page_lines: list[list[dict[str, Any]]]) -> set[str]:
        """Lines repeated on many pages are running headers/footers."""
        if len(page_lines) < 4:
            return set()
        counts: Counter[str] = Counter()
        for lines in page_lines:
            seen = {ln["text"].strip() for ln in lines[:2] + lines[-2:] if ln["text"].strip()}
            counts.update(seen)
        threshold = max(2, int(len(page_lines) * HEADER_RATIO))
        return {text for text, c in counts.items() if c >= threshold}

    def _is_furniture(self, line: dict[str, Any], furniture: set[str], pno: int) -> bool:
        text = line["text"].strip()
        if text in furniture:
            return True
        # bare page numbers
        if text.isdigit() and len(text) <= 4:
            return True
        return False
