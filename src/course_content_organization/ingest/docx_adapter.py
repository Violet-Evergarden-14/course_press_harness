"""Word adapter (docs/ingest.md §6).

python-docx is imported lazily so that a missing optional dependency degrades to a
clear runtime message instead of breaking `study` for everybody.

Word has no page coordinates, so the locator is the paragraph index. Heading styles
carry the structure; `Heading N` maps to title level N.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from ..ids import src_key
from ..ir import Block
from . import register
from .base import Adapter, IngestResult

HEADING_STYLE_RE = re.compile(r"^(?:Heading|标题)\s*(\d)$", re.IGNORECASE)
LIST_STYLE_RE = re.compile(r"^(?:List|列表)", re.IGNORECASE)


def _require_docx():
    try:
        import docx  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "python-docx 未安装：uv pip install python-docx（或 pip install study-harness[docx]）"
        ) from exc
    return docx


@register
class DocxAdapter(Adapter):
    name = "docx"
    accepts = (".docx",)

    def probe(self, path: Path) -> dict[str, Any]:
        try:
            docx = _require_docx()
            doc = docx.Document(str(path))
        except Exception as exc:  # noqa: BLE001
            return {"present": True, "valid": False, "error": str(exc)}
        paras = [p for p in doc.paragraphs if p.text.strip()]
        headings = sum(1 for p in paras if HEADING_STYLE_RE.match((p.style.name or "").strip()))
        return {
            "present": True,
            "size": path.stat().st_size,
            "paragraphs": len(paras),
            "headings": headings,
            "tables": len(doc.tables),
            "images": len(doc.inline_shapes),
            "valid": True,
        }

    def ingest(self, path: Path, ctx: Any) -> IngestResult:
        res = IngestResult()
        docx = _require_docx()
        doc = docx.Document(str(path))
        key = src_key(path.name, self.name)
        assets_dir: Path = ctx.assets_dir

        n = 0
        for idx, para in enumerate(doc.paragraphs):
            text = (para.text or "").strip()
            if not text:
                continue
            style = (para.style.name or "").strip() if para.style is not None else ""

            n += 1
            hm = HEADING_STYLE_RE.match(style)
            if hm:
                res.blocks.append(Block(
                    id=f"{key}-p{idx:04d}", src=path.name, type="title",
                    level=int(hm.group(1)), loc={"para": idx}, text=text,
                    extra={"style": style}))
                continue

            level = 1
            if LIST_STYLE_RE.match(style) and para.paragraph_format.left_indent:
                level = max(1, int(round(para.paragraph_format.left_indent.pt / 18)))

            res.blocks.append(Block(
                id=f"{key}-p{idx:04d}", src=path.name, type="bullet", level=level,
                loc={"para": idx}, text=text,
                extra={"style": style} if style and style != "Normal" else None))

        # tables, in document order relative to paragraphs where possible
        for ti, table in enumerate(doc.tables, start=1):
            rows = []
            for row in table.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                if any(cells):
                    rows.append(cells)
            if rows:
                n += 1
                res.blocks.append(Block(
                    id=f"{key}-t{ti:03d}", src=path.name, type="table",
                    loc={"table": ti}, rows=rows))

        # inline images
        for si, shape in enumerate(doc.inline_shapes, start=1):
            try:
                blob = shape._inline.graphic.graphicData.pic.blipFill.blip.embed
                part = doc.part.related_parts[blob]
                data = part.blob
            except Exception as exc:  # noqa: BLE001
                res.warnings.append({"stage": "docx/image", "severity": "warning",
                                     "msg": f"图片导出失败: {exc}", "inline": si})
                continue
            digest = hashlib.sha256(data).hexdigest()[:8]
            # PackURI exposes `ext` (".png"); fall back to png if the part is unusual
            ext = str(getattr(getattr(part, "partname", None), "ext", "") or ".png")
            ext = ext.lower().lstrip(".") or "png"
            assets_dir.mkdir(parents=True, exist_ok=True)
            target = assets_dir / f"{digest}.{ext}"
            if not target.exists():
                target.write_bytes(data)
            n += 1
            res.blocks.append(Block(
                id=f"{key}-i{si:03d}", src=path.name, type="image",
                loc={"inline": si}, asset=f"assets/{target.name}"))

        res.capabilities = self.probe(path)
        return res
