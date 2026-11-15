"""Figures: extracted assets vs. redrawn / synthesized vector specs.

Two origins are supported (docs/rendering.md §9):

* `extracted` — lifted verbatim from the source material, stored content-hashed.
* `redrawn`   — same content as the source, re-expressed as text vector so it can be
                diffed, reviewed and edited (Mermaid / Graphviz / SVG / TikZ).
* `synthesized` — a figure that does not exist in the source and was added to explain.

Text formats are preferred over raster precisely because they are reviewable: a model
that draws a diagram is producing a claim about the material, and a claim has to be
checkable.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIGURES_DIR = ".study/figures"

# format -> (file extension, markdown emission)
TEXT_FORMATS = {
    "mermaid": (".mmd", "fence"),
    "dot": (".dot", "fence"),
    "tikz": (".tex", "fence"),
    "svg": (".svg", "image"),
}
RASTER_FORMATS = {"png": ".png", "jpg": ".jpg", "jpeg": ".jpeg"}

PROVENANCE = {"extracted", "redrawn", "synthesized"}

FENCE_LANG = {"mermaid": "mermaid", "dot": "dot", "tikz": "latex"}

# file suffix / loose names -> canonical format
FORMAT_ALIASES = {
    "mmd": "mermaid", "mermaid": "mermaid", "mermaid.md": "mermaid",
    "dot": "dot", "gv": "dot", "graphviz": "dot",
    "tex": "tikz", "tikz": "tikz",
    "svg": "svg",
    "png": "png", "jpg": "jpg", "jpeg": "jpeg",
}


def canonical_format(name: str | None) -> str:
    if not name:
        return "mermaid"
    return FORMAT_ALIASES.get(name.lower().lstrip("."), name.lower().lstrip("."))


@dataclass
class Figure:
    id: str
    path: Path
    format: str
    provenance: str = "redrawn"
    evidence: list[dict[str, Any]] = field(default_factory=list)
    by: str | None = None
    reviewed: bool = False
    caption: str | None = None

    @property
    def sidecar(self) -> Path:
        return self.path.with_suffix(".json")

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "format": self.format,
            "provenance": self.provenance,
            "evidence": self.evidence,
            "by": self.by,
            "reviewed": self.reviewed,
            "caption": self.caption,
            "file": self.path.name,
        }

    def emit(self, asset_prefix: str = "book.assets") -> str:
        """Markdown for this figure.

        Mermaid renders natively on GitHub/GitLab, so a redrawn diagram ships as a
        fence: readable in the diff, rendered on the platform, never a binary blob.
        """
        if self.format in TEXT_FORMATS and TEXT_FORMATS[self.format][1] == "image":
            return f"![{self.caption or self.id}]({asset_prefix}/{self.path.name})"
        if self.format in FENCE_LANG:
            body = self.path.read_text(encoding="utf-8").strip()
            return f"```{FENCE_LANG[self.format]}\n{body}\n```"
        if self.format in RASTER_FORMATS:
            return f"![{self.caption or self.id}]({asset_prefix}/{self.path.name})"
        return f"![{self.caption or self.id}]({asset_prefix}/{self.path.name})"

    def binary(self) -> bool:
        return self.format in RASTER_FORMATS or self.format == "svg"


def load_figures(workspace: Path) -> dict[str, Figure]:
    d = workspace / FIGURES_DIR
    if not d.exists():
        return {}
    out: dict[str, Figure] = {}
    for sidecar in sorted(d.glob("*.json")):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        fid = meta.get("id") or sidecar.stem
        target = d / (meta.get("file") or f"{fid}.mmd")
        if not target.exists():
            continue
        out[fid] = Figure(
            id=fid, path=target,
            format=canonical_format(meta.get("format") or target.suffix),
            provenance=meta.get("provenance", "redrawn"), evidence=meta.get("evidence", []),
            by=meta.get("by"), reviewed=bool(meta.get("reviewed")), caption=meta.get("caption"),
        )
    return out


def add_figure(workspace: Path, fid: str, source: Path, *, fmt: str | None = None,
               provenance: str = "redrawn", caption: str | None = None,
               evidence: list[dict[str, Any]] | None = None, by: str | None = None) -> Figure:
    if provenance not in PROVENANCE:
        raise ValueError(f"provenance must be one of {sorted(PROVENANCE)}")
    d = workspace / FIGURES_DIR
    d.mkdir(parents=True, exist_ok=True)
    fmt = canonical_format(fmt or source.suffix)
    ext = TEXT_FORMATS[fmt][0] if fmt in TEXT_FORMATS else RASTER_FORMATS.get(fmt, source.suffix)
    target = d / f"{fid}{ext}"
    shutil.copyfile(source, target)
    fig = Figure(id=fid, path=target, format=fmt, provenance=provenance,
                 evidence=evidence or [], by=by, caption=caption)
    target.with_suffix(".json").write_text(
        json.dumps(fig.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return fig


def request_figure(workspace: Path, unit_id: str, description: str,
                   *, provenance: str = "redrawn",
                   evidence: list[dict[str, Any]] | None = None,
                   preferred: str = "mermaid") -> Path:
    """Write a `figure_request` gap: the description of what is missing."""
    gaps = workspace / ".study" / "gaps"
    gaps.mkdir(parents=True, exist_ok=True)
    fid = f"fig-{unit_id}".replace(" ", "-").lower()
    payload = {
        "id": fid,
        "kind": "figure_request",
        "unit": unit_id,
        "provenance": provenance,
        "description": description,
        "answer_format": {
            "format": preferred if preferred in TEXT_FORMATS else "mermaid",
            "content": "str  # 图形规格文本，不要位图",
            "caption": "str",
        },
        "evidence": evidence or [],
        "rules": [
            "优先使用文本矢量格式（mermaid/dot/svg/tikz），禁止仅用位图",
            "图中出现的文字必须是中文术语表中的写法",
            "不得在图中标注出处（课件、页码、录音时间）",
        ],
    }
    path = gaps / f"{fid}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
