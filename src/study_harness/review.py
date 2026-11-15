"""L2 review: per-claim entailment against the unit's own evidence.

This is the mechanism that answers "is the model making things up?". It is split in
two deterministic halves with a gap in between (docs/review-protocol.md §4):

    prepare()  unit -> claims + evidence -> .study/gaps/l2-<unit>.json
    apply()    answers -> verdicts -> findings + recomputed `origin`

Neither half calls a model, and neither half edits prose. `apply` only writes
metadata (`origin`, `reviewed_by`) and findings; deleting or rewriting a claim stays
a human decision.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .ir import sha256_text
from .knowledge import Unit, load_units

VERDICTS = {"supported", "derived", "unsupported"}
ORIGIN_RANK = {"source": 0, "derived": 1, "synthesized": 2}

# lines that are not assertions: images, formulas, placeholders, list markers only
SKIP_RE = re.compile(r"^(!\[|\[\[asset:|\[\[fig:|\$\$|```|\s*$)")
MARKUP_RE = re.compile(r"[*_`#>]")
PLACEHOLDER_RE = re.compile(r"^（[^）]*(待补充|无可提取)[^）]*）$")


def split_claims(unit: Unit) -> list[dict[str, Any]]:
    """Deterministic claim extraction: one non-empty prose line per claim."""
    claims: list[dict[str, Any]] = []
    idx = 0
    for section, lines in unit.sections.items():
        if section in {"图示", "图示说明", "表格", "备注"}:
            continue
        for raw in lines:
            text = raw.strip()
            if not text or SKIP_RE.match(text) or PLACEHOLDER_RE.match(text):
                continue
            clean = MARKUP_RE.sub("", text).strip()
            if len(clean) < 4:
                continue
            claims.append({"claim_idx": idx, "section": section, "text": clean})
            idx += 1
    return claims


def _evidence_text(src: str, loc: dict[str, Any], blocks_by_src: dict[str, list[dict]]) -> str:
    out: list[str] = []
    for b in blocks_by_src.get(src, []):
        if b.get("loc") == loc or (
            isinstance(loc.get("slide"), int) and b.get("loc", {}).get("slide") == loc["slide"]
        ) or (
            isinstance(loc.get("page"), int) and b.get("loc", {}).get("page") == loc["page"]
        ):
            if b.get("type") in {"bullet", "title", "formula", "table"}:
                text = b.get("text") or b.get("latex") or ""
                if text:
                    out.append(str(text).strip())
    return "\n".join(out)[:4000]


def gap_id(unit_id: str) -> str:
    return f"l2-{unit_id}"


def prepare(workspace: Path, units: list[Unit], blocks_by_src: dict[str, list[dict]],
            *, only: str | None = None, limit: int | None = None) -> list[Path]:
    gaps_dir = workspace / ".study" / "gaps"
    gaps_dir.mkdir(parents=True, exist_ok=True)
    answered = {p.stem for p in (gaps_dir / "answers").glob("*.json")} if (gaps_dir / "answers").exists() else set()

    previous_titles: list[str] = []
    created: list[Path] = []
    for unit in units:
        titles_context = list(previous_titles[-12:])
        previous_titles.append(f"{unit.title}：{_first_line(unit)}")
        if only and unit.id != only:
            continue
        gid = gap_id(unit.id)
        if gid in answered:
            continue
        claims = split_claims(unit)
        if not claims:
            continue
        if limit is not None and len(created) >= limit:
            break

        evidence = []
        for e in unit.evidence:
            text = _evidence_text(e.get("src", ""), e.get("loc") or {}, blocks_by_src)
            if text:
                evidence.append({"src": e.get("src"), "loc": e.get("loc"), "text": text})
        payload = {
            "id": gid,
            "kind": "claim_entailment",
            "unit": unit.id,
            "input": {
                "claims": [{"claim_idx": c["claim_idx"], "text": c["text"]} for c in claims],
                "evidence": evidence,
                "book_context": titles_context,
            },
            "instruction_version": 1,
            "answer_format": {
                "verdicts": "[{claim_idx: int, verdict: supported|derived|unsupported, quote: str, reason?: str}]",
                "by": "str",
                "confidence": "str  # high | medium | low",
            },
            "rules": [
                "只判定，不改写正文",
                "quote 必须是原断言中的逐字片段，用于校验切分是否对齐",
                "unsupported 必须给出 reason",
            ],
            "input_hash": sha256_text(json.dumps(
                {"unit": unit.id, "claims": claims}, ensure_ascii=False, sort_keys=True)),
        }
        path = gaps_dir / f"{gid}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created.append(path)
    return created


def _first_line(unit: Unit) -> str:
    for _, lines in unit.sections.items():
        for line in lines:
            text = MARKUP_RE.sub("", line).strip()
            if text and not SKIP_RE.match(text):
                return text[:80]
    return ""


def collect_answers(workspace: Path) -> dict[str, dict[str, Any]]:
    d = workspace / ".study" / "gaps" / "answers"
    if not d.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(d.glob("l2-*.json")):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    return out


def apply(workspace: Path, units_dir: Path, units: list[Unit]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read answers, emit findings, recompute `origin`. Never edits prose."""
    answers = collect_answers(workspace)
    findings: list[dict[str, Any]] = []
    stats = {"units_reviewed": 0, "claims": 0, "supported": 0, "derived": 0, "unsupported": 0}

    for unit in units:
        gid = gap_id(unit.id)
        ans = answers.get(gid)
        if not ans:
            continue
        claims = {c["claim_idx"]: c for c in split_claims(unit)}
        verdicts = ans.get("verdicts") or []
        if not verdicts:
            continue
        stats["units_reviewed"] += 1

        worst = "source"
        for v in verdicts:
            ci = v.get("claim_idx")
            claim = claims.get(ci)
            if claim is None:
                findings.append(_finding("claim_mismatch", "info", unit.id,
                                         f"回答引用了不存在的断言 #{ci}",
                                         hint="重新生成该知识点的缺口"))
                continue
            quote = (v.get("quote") or "").strip()
            if quote and quote not in claim["text"] and claim["text"] not in quote:
                findings.append(_finding("claim_mismatch", "info", unit.id,
                                         f"断言 #{ci} 的 quote 与原文不一致，已忽略该条判定",
                                         hint="检查缺口切分与回答是否对应"))
                continue

            verdict = v.get("verdict")
            if verdict not in VERDICTS:
                findings.append(_finding("claim_mismatch", "info", unit.id,
                                         f"断言 #{ci} 的 verdict 非法：{verdict}"))
                continue
            stats["claims"] += 1
            stats[verdict] += 1

            if verdict == "unsupported":
                worst = _weaker(worst, "synthesized")
                findings.append(_finding(
                    "unsupported_claim", "major", unit.id,
                    f"断言 #{ci} 无证据支撑：{claim['text'][:60]}",
                    hint=(v.get("reason") or "删除该断言，或标注为补充内容")))
            elif verdict == "derived":
                worst = _weaker(worst, "derived")

        # metadata only: reflect what the verdicts say about the unit's basis
        target_origin = worst
        if ORIGIN_RANK[target_origin] > ORIGIN_RANK.get(unit.origin, 0):
            findings.append(_finding(
                "origin_mismatch", "major", unit.id,
                f"origin={unit.origin}，但审查结论要求 {target_origin}",
                hint="应用审查后重算 origin"))
            unit.origin = target_origin
            unit.revision += 1
            _save(units_dir, unit)
        if "l2" not in unit.reviewed_by:
            unit.reviewed_by.append("l2")
            _save(units_dir, unit)

    return findings, stats


def retag_origins(workspace: Path, units_dir: Path, units: list[Unit]) -> int:
    """Re-derive `origin` / `reviewed_by` from stored verdicts after a rebuild.

    Draft units are regenerated by `build`, which would otherwise erase review
    results. Deriving the metadata from the answers (which live outside the unit)
    keeps it reproducible: same answers, same origin, every run.
    """
    answers = collect_answers(workspace)
    if not answers:
        return 0
    changed = 0
    for unit in units:
        ans = answers.get(gap_id(unit.id))
        if not ans:
            continue
        verdicts = [v.get("verdict") for v in ans.get("verdicts") or []]
        target = "source"
        for v in verdicts:
            if v == "unsupported":
                target = "synthesized"
                break
            if v == "derived":
                target = _weaker(target, "derived")
        touched = False
        if ORIGIN_RANK[target] > ORIGIN_RANK.get(unit.origin, 0):
            unit.origin = target
            touched = True
        if "l2" not in unit.reviewed_by:
            unit.reviewed_by.append("l2")
            touched = True
        if touched:
            _save(units_dir, unit)
            changed += 1
    return changed


def _weaker(a: str, b: str) -> str:
    return a if ORIGIN_RANK[a] >= ORIGIN_RANK[b] else b


def _save(units_dir: Path, unit: Unit) -> None:
    (units_dir / f"{unit.id}.md").write_text(unit.to_markdown(), encoding="utf-8", newline="\n")


def _finding(gate: str, level: str, unit: str, message: str, *, hint: str | None = None) -> dict[str, Any]:
    d = {"gate": gate, "level": level, "unit": unit, "message": message}
    if hint:
        d["hint"] = hint
    return d


def load_units_for_review(units_dir: Path) -> list[Unit]:
    return load_units(units_dir)


def stats_from_answers(workspace: Path) -> dict[str, Any]:
    answers = collect_answers(workspace)
    total = {"claims": 0, "supported": 0, "derived": 0, "unsupported": 0}
    for ans in answers.values():
        for v in ans.get("verdicts") or []:
            verdict = v.get("verdict")
            if verdict in VERDICTS:
                total["claims"] += 1
                total[verdict] += 1
    claims = total["claims"]
    return {
        **total,
        "unsupported_ratio": round(total["unsupported"] / claims, 3) if claims else None,
        "derived_ratio": round(total["derived"] / claims, 3) if claims else None,
        "units_answered": len(answers),
    }
