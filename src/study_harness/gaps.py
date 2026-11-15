"""Gap protocol: the only place where a model (or a human) enters the pipeline.

The CLI never calls a model. When something needs understanding it writes a gap file;
any agent or person fills `answers/<id>.json`; the next build reads it back. This is
what keeps the pipeline reproducible and vendor neutral.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ir import sha256_text

IMAGE_QUESTION = (
    "这张图里承载了哪些正文没有写出的信息？"
    "若图中有文字，先完整转写；若图表达结构（节点、边、层级），给出节点集合与关系；"
    "若图表达趋势或数值，给出关键点。无法判断时回答 unknown。"
)


def _gaps_dir(workspace: Path) -> Path:
    return workspace / ".study" / "gaps"


def register_image_gaps(workspace: Path, blocks: list[dict[str, Any]],
                        units: list[Any], *, limit: int | None = None) -> list[Path]:
    """One gap per image that carries no semantics yet.

    The question is written per image using its surrounding text, because "describe
    this picture" produces unusable answers: the model needs to know what is missing.
    """
    d = _gaps_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)

    by_src: dict[str, list[dict[str, Any]]] = {}
    for b in blocks:
        by_src.setdefault(b.get("src", ""), []).append(b)

    unit_by_pos: dict[tuple[str, int], str] = {}
    for u in units:
        for e in getattr(u, "evidence", []):
            loc = e.get("loc") or {}
            for key in ("slide", "page"):
                if isinstance(loc.get(key), int):
                    unit_by_pos[(e.get("src", ""), loc[key])] = u.id
                    break

    created: list[Path] = []
    for b in blocks:
        if b.get("type") != "image":
            continue
        if b.get("semantics"):
            continue
        loc = b.get("loc") or {}
        pos = loc.get("slide") if "slide" in loc else loc.get("page")
        if pos is None:
            continue
        gap_id = f"img-{b.get('id')}".replace(" ", "-")
        path = d / f"{gap_id}.json"
        if path.exists():
            created.append(path)
            continue
        if limit is not None and len(created) >= limit:
            break

        unit_id = unit_by_pos.get((b.get("src", ""), pos))
        context_blocks = by_src.get(b.get("src", ""), [])
        nearby = [x.get("text") for x in context_blocks
                  if x.get("type") == "bullet" and x.get("loc", {}).get("slide") == pos]
        payload = {
            "id": gap_id,
            "kind": "image_semantics" if b.get("asset") else "page_text",
            "unit": unit_id,
            "asset": b.get("asset"),
            "loc": loc,
            "question": IMAGE_QUESTION if b.get("asset") else
                        "这一页没有文字层，请转写页面上的全部文字内容。",
            "answer_format": {
                "text": "str  # 图中文字，逐字转写",
                "relations": "[[源, 目标], ...]  # 可选，结构图填写",
                "summary": "str  # 一句话说明这张图在讲什么",
            },
            "context": {"nearby_text": [t for t in nearby if t][:6],
                        "unit_hint": unit_id},
            "input_hash": sha256_text(json.dumps(b, sort_keys=True, ensure_ascii=False)),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created.append(path)
    return created


def load_answers(workspace: Path) -> dict[str, dict[str, Any]]:
    d = _gaps_dir(workspace) / "answers"
    if not d.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(d.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    return out


def fill_gap(workspace: Path, gap_id: str, payload: dict[str, Any]) -> Path:
    gap = _gaps_dir(workspace) / f"{gap_id}.json"
    if not gap.exists():
        raise FileNotFoundError(gap_id)
    spec = json.loads(gap.read_text(encoding="utf-8"))
    fmt = spec.get("answer_format") or {}
    unknown = [k for k in payload if k not in fmt and k not in ("by", "confidence", "unknown")]
    if unknown:
        raise ValueError(f"答案含未声明字段: {', '.join(unknown)}；允许: {', '.join(fmt) or '任意'}")
    d = _gaps_dir(workspace) / "answers"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{gap_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def apply_answers(blocks: list[dict[str, Any]], answers: dict[str, dict[str, Any]]) -> int:
    """Attach filled answers back onto image blocks so drafts can use them."""
    applied = 0
    for b in blocks:
        if b.get("type") != "image":
            continue
        gap_id = f"img-{b.get('id')}".replace(" ", "-")
        ans = answers.get(gap_id)
        if not ans:
            continue
        summary = ans.get("summary") or ans.get("text")
        if not summary:
            continue
        b["semantics"] = summary if not ans.get("relations") else {
            "summary": summary, "relations": ans.get("relations"), "text": ans.get("text"),
        }
        b["semantics_by"] = ans.get("by") or "human"
        applied += 1
    return applied


def open_gaps(workspace: Path) -> list[Path]:
    d = _gaps_dir(workspace)
    if not d.exists():
        return []
    answered = {p.stem for p in (d / "answers").glob("*.json")} if (d / "answers").exists() else set()
    return [p for p in sorted(d.glob("*.json")) if p.stem not in answered]
