"""Compose: turn draft material into text a student can actually learn from.

Drafts are verbatim slide fragments. Composing replaces them with teaching prose.

Two kinds of rules, deliberately kept apart:

* **hard rules** — facts about the pipeline, not about taste: never invent, never cite
  the source, drop administrative material, only keep figures that teach. Breaking
  these makes the output unusable or dishonest.
* **style** — the user's own words (`compose.style`, optional `compose.example`).
  Tone, density, how much history to keep, whether to open with motivation: these are
  editorial choices and belong to the person paying for the book, not to this file.

If no style is given, composing refuses to run rather than inventing one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .knowledge import Unit, clean_text

GAP_PREFIX = "compose-"

HARD_RULES = [
    "不许编造：原文没有的事实、例子、数字，一律不写。拿不准就删掉。",
    "不许出现来源指称：不写「课件上」「第 X 页」「老师讲过」，全部用本书自己的口吻。",
    "删掉不承载知识的内容：作者、单位、邮箱、网址、课程安排、致谢、软件操作步骤。",
    "删掉重复：同一结论出现两次时，只保留讲得最清楚的那一次。",
    "不要照抄原文条目顺序；按读者理解的顺序重排。",
    "不要写死节号（「见 6.4 节」「6.6 讲过的」）：书里的节号会随增删变化，"
    "正确写法是 [[unit-id]] 或直接说「后面讲转换时」这种自然指代。",
    "原文已经写清楚的定义直接沿用，把篇幅留给它没讲明白的地方。",
]

FIGURE_RULES = [
    "只保留承载核心概念的图。装饰图、作者照片、软件截图一律不要。",
    "保留原图时，必须能说出它在解释什么；说不出来就不要保留。",
    "结构图、流程图、状态机、对比图优先重画为 mermaid 文本图，不要放位图原图。",
    "重画时图中文字用中文术语，与正文一致，且不标注出处。",
]

STYLE_HINT = (
    "写作要求由使用者提供（study.config.yaml 的 compose.style）：文风、详略、"
    "保留什么舍弃什么，都按使用者的说明来。这里不预设任何风格，也不提供样例。"
)


def gap_id(unit_id: str) -> str:
    return f"{GAP_PREFIX}{unit_id}".replace(" ", "-").lower()


def prepare(workspace: Path, units: list[Unit], *, style: str, example: str | None = None,
            only: str | None = None, limit: int | None = None) -> list[Path]:
    gaps = workspace / ".study" / "gaps"
    gaps.mkdir(parents=True, exist_ok=True)
    answers = {p.stem for p in (gaps / "answers").glob("*.json")} if (gaps / "answers").exists() else set()

    created: list[Path] = []
    for unit in units:
        if only and unit.id != only:
            continue
        gid = gap_id(unit.id)
        if gid in answers:
            continue
        if limit is not None and len(created) >= limit:
            break

        raw_sections = {
            name: [clean_text(line.lstrip("- ").strip()) for line in lines if line.strip()]
            for name, lines in unit.sections.items()
        }
        if not raw_sections:
            continue
        images = [line for name, lines in unit.sections.items() if name == "图示"
                  for line in lines]

        payload = {
            "id": gid,
            "kind": "compose_unit",
            "unit": unit.id,
            "current_title": unit.title,
            "style": style,
            "style_example": example or "",
            "hard_rules": HARD_RULES,
            "figure_rules": FIGURE_RULES,
            "input": {
                "raw_sections": raw_sections,
                "images": images,
                "evidence_locs": [e.get("loc") for e in unit.evidence][:20],
            },
            "answer_format": {
                "title": "str  # 面向读者的标题，去掉章节编号",
                "sections": "{'概念': [str], '为什么需要': [str], '怎么用': [str], "
                            "'例子': [str], '易错': [str]}  # 只写有内容的，小节名可自定",
                "keep_images": "[str]  # 必须保留的原图 id",
                "redraw": "[{id, format: mermaid|svg, description, caption}]",
                "dropped": "[str]  # 删掉的原文要点，便于作者核对",
                "origin": "derived | synthesized",
                "by": "str",
            },
        }
        path = gaps / f"{gid}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created.append(path)
    return created


def apply(workspace: Path, units_dir: Path, units: list[Unit]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replace draft sections with composed prose; keep evidence, bump revision."""
    answers_dir = workspace / ".study" / "gaps" / "answers"
    findings: list[dict[str, Any]] = []
    stats = {"composed": 0, "skipped": 0}
    if not answers_dir.exists():
        return findings, stats

    by_id = {u.id: u for u in units}
    for f in sorted(answers_dir.glob(f"{GAP_PREFIX}*.json")):
        try:
            ans = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        uid = f.stem[len(GAP_PREFIX):]
        unit = by_id.get(uid)
        if unit is None:
            findings.append({"gate": "compose_orphan", "level": "info",
                             "unit": uid, "message": "答案对应的知识点已不存在"})
            stats["skipped"] += 1
            continue

        sections = {k: [str(x) for x in v] for k, v in (ans.get("sections") or {}).items() if v}
        if not sections:
            stats["skipped"] += 1
            continue

        unit.sections = sections
        if ans.get("title"):
            unit.title = str(ans["title"]).strip()
        unit.origin = ans.get("origin") if ans.get("origin") in {"derived", "synthesized"} else "synthesized"
        unit.draft = False
        unit.revision += 1
        if "compose" not in unit.reviewed_by:
            unit.reviewed_by.append("compose")
        (units_dir / f"{unit.id}.md").write_text(unit.to_markdown(), encoding="utf-8", newline="\n")

        stats["composed"] += 1
        dropped = ans.get("dropped") or []
        if dropped:
            findings.append({"gate": "compose_dropped", "level": "info", "unit": uid,
                             "message": f"合成时删去 {len(dropped)} 条原文要点",
                             "hint": "；".join(str(d)[:40] for d in dropped[:3])})
        for fig in ans.get("redraw") or []:
            findings.append({"gate": "figure_redraw_request", "level": "minor", "unit": uid,
                             "message": f"建议重画图：{str(fig.get('description', ''))[:50]}",
                             "hint": str(fig.get("format", "mermaid"))})
    return findings, stats
