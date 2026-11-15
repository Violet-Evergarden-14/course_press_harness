"""On-demand explanations ("I don't understand this section").

Remedies are *not* written into knowledge units. They live in `patches/remedial/`,
carry `origin: synthesized`, and are overlaid at render time — so deleting a patch
restores the original text, and updating the course never fights with personal
notes (docs/architecture.md §13).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import yaml

from .knowledge import Unit

GAP_PREFIX = "remedial-"
PATCH_DIR = ".study/patches/remedial"


def _slug(unit_id: str) -> str:
    return unit_id.strip().lower().replace(" ", "-")


def gap_id(unit_id: str) -> str:
    return f"{GAP_PREFIX}{_slug(unit_id)}"


def request(workspace: Path, unit: Unit, *, focus: str | None = None,
            prereq: bool = False) -> Path:
    """Write a remedial gap describing exactly what is missing."""
    gaps = workspace / ".study" / "gaps"
    gaps.mkdir(parents=True, exist_ok=True)
    gid = gap_id(unit.id)
    kind = "prereq" if prereq else "explanation"
    question = (
        f"读者表示在《{unit.title}》之前的基础概念上存在缺口，请只补充前置知识，"
        f"不要复述本节已经讲清楚的内容。"
        if prereq else
        f"读者表示没读懂《{unit.title}》。请用更小的步骤重新讲解，"
        f"保留原有结论但补上推导过程与一个可手算的例子。"
    )
    payload = {
        "id": gid,
        "kind": "remedial",
        "unit": unit.id,
        "title": unit.title,
        "kind_hint": kind,
        "focus": focus,
        "question": question,
        "input": {
            "sections": {name: lines for name, lines in unit.sections.items()},
            "symbols": unit.symbols,
            "depends_on": unit.depends_on,
        },
        "answer_format": {
            "markdown": "str  # 直接可排版的 Markdown，不要写解释性的元话语",
            "by": "str",
        },
        "rules": [
            "只讲这一个知识点，不要扩展成整章",
            "不使用读者可能没有的资料作为依据，也不要提及出处",
            "标注 origin: synthesized —— 这段不是课程原有内容",
            "篇幅控制在 300~800 字；写不出可手算的例子就不要硬编",
        ],
    }
    path = gaps / f"{gid}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def apply(workspace: Path) -> list[Path]:
    """Turn filled remedial answers into patch files."""
    answers_dir = workspace / ".study" / "gaps" / "answers"
    if not answers_dir.exists():
        return []
    out_dir = workspace / PATCH_DIR
    written: list[Path] = []

    for f in sorted(answers_dir.glob(f"{GAP_PREFIX}*.json")):
        try:
            ans = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        markdown = (ans.get("markdown") or "").strip()
        if not markdown:
            continue
        unit_id = f.stem[len(GAP_PREFIX):]
        out_dir.mkdir(parents=True, exist_ok=True)
        header = yaml.safe_dump({
            "unit": unit_id,
            "origin": "synthesized",
            "requested_by": "student",
            "by": ans.get("by") or None,
        }, allow_unicode=True, sort_keys=False).strip()
        path = out_dir / f"{unit_id}.md"
        path.write_text(f"---\n{header}\n---\n\n{markdown}\n", encoding="utf-8", newline="\n")
        written.append(path)
    return written


def load(workspace: Path) -> dict[str, str]:
    """unit id -> markdown body, for render-time overlay."""
    d = workspace / PATCH_DIR
    if not d.exists():
        return {}
    out: dict[str, str] = {}
    for f in sorted(d.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            body = parts[2] if len(parts) > 2 else ""
            meta: dict[str, Any] = {}
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except Exception:
                meta = {}
            unit = meta.get("unit") or f.stem
        else:
            unit, body = f.stem, text
        body = body.strip()
        if body:
            out[unit] = body
    return out


def remove(workspace: Path, unit_id: str) -> bool:
    for path in (
        workspace / PATCH_DIR / f"{_slug(unit_id)}.md",
        workspace / ".study" / "gaps" / f"{gap_id(unit_id)}.json",
        workspace / ".study" / "gaps" / "answers" / f"{gap_id(unit_id)}.json",
    ):
        if path.exists():
            path.unlink()
            removed = True
    return (workspace / PATCH_DIR / f"{_slug(unit_id)}.md").exists() is False
