"""L2 claim entailment: prepare -> answer -> apply (docs/review-protocol.md §4)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from course_content_organization.cli import main
from course_content_organization.review import apply, gap_id, prepare, split_claims, stats_from_answers


def _ws(tmp_path: Path, pptx: Path) -> Path:
    ws = tmp_path / "ws"
    main(["--workspace", str(ws), "init", "--course", "示例课程"])
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / "inputs" / pptx.name).write_bytes(pptx.read_bytes())
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"] = [{"number": 6, "title": "示例主题 A",
                                  "sources": [f"inputs/{pptx.name}"]}]
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    main(["--workspace", str(ws), "build"])
    return ws


def test_claims_skip_non_prose(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    from course_content_organization.knowledge import load_units

    units = load_units(ws / ".study" / "knowledge" / "units")
    claims = split_claims(units[0])
    assert claims, "prose lines must become claims"
    assert all(c["text"] and not c["text"].startswith("![") for c in claims)
    assert [c["claim_idx"] for c in claims] == list(range(len(claims)))


def test_prepare_writes_one_gap_per_unit(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    from course_content_organization.knowledge import load_units

    units = load_units(ws / ".study" / "knowledge" / "units")
    created = prepare(ws, units, {})
    assert len(created) == len(units)
    spec = json.loads(created[0].read_text(encoding="utf-8"))
    assert spec["kind"] == "claim_entailment"
    assert spec["input"]["claims"]
    assert any("不改写正文" in r for r in spec["rules"])


def test_apply_recomputes_origin_and_never_edits_prose(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    from course_content_organization.knowledge import load_units

    units_dir = ws / ".study" / "knowledge" / "units"
    units = load_units(units_dir)
    unit = next(u for u in units if u.sections.get("说明"))
    prepare(ws, units, {}, only=unit.id)

    claims = split_claims(unit)
    answers_dir = ws / ".study" / "gaps" / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "verdicts": [
            {"claim_idx": claims[0]["claim_idx"], "verdict": "supported",
             "quote": claims[0]["text"][:10]},
            {"claim_idx": claims[1]["claim_idx"], "verdict": "unsupported",
             "quote": claims[1]["text"][:10], "reason": "证据中没有这一句"},
        ],
        "by": "tester",
    }
    (answers_dir / f"{gap_id(unit.id)}.json").write_text(json.dumps(payload, ensure_ascii=False),
                                                         encoding="utf-8")

    body_before = {s: list(lines) for s, lines in unit.sections.items()}
    findings, stats = apply(ws, units_dir, load_units(units_dir))

    after = {u.id: u for u in load_units(units_dir)}[unit.id]
    assert after.sections == body_before, "review must not rewrite the prose"
    assert after.origin == "synthesized", "an unsupported claim forces the weakest origin"
    assert "l2" in after.reviewed_by
    assert after.revision > unit.revision
    assert any(f["gate"] == "unsupported_claim" and f["level"] == "major" for f in findings)
    assert any(f["gate"] == "origin_mismatch" for f in findings)
    assert stats["unsupported"] == 1


def test_bad_quote_is_ignored_not_crashing(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    from course_content_organization.knowledge import load_units

    units_dir = ws / ".study" / "knowledge" / "units"
    units = load_units(units_dir)
    unit = units[0]
    prepare(ws, units, {}, only=unit.id)
    answers_dir = ws / ".study" / "gaps" / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    (answers_dir / f"{gap_id(unit.id)}.json").write_text(json.dumps({
        "verdicts": [{"claim_idx": 0, "verdict": "unsupported", "quote": "根本不存在的句子"}],
        "by": "tester",
    }, ensure_ascii=False), encoding="utf-8")

    findings, stats = apply(ws, units_dir, load_units(units_dir))
    assert any(f["gate"] == "claim_mismatch" for f in findings)
    assert stats["claims"] == 0 and stats["unsupported"] == 0


def test_stats_report_ratios(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    from course_content_organization.knowledge import load_units

    units = load_units(ws / ".study" / "knowledge" / "units")
    unit = next(u for u in units if len(split_claims(u)) >= 2)
    prepare(ws, units, {}, only=unit.id)
    claims = split_claims(unit)
    answers_dir = ws / ".study" / "gaps" / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    (answers_dir / f"{gap_id(unit.id)}.json").write_text(json.dumps({
        "verdicts": [
            {"claim_idx": c["claim_idx"], "verdict": "supported", "quote": c["text"][:8]}
            for c in claims[:1]
        ] + [
            {"claim_idx": c["claim_idx"], "verdict": "derived", "quote": c["text"][:8]}
            for c in claims[1:2]
        ],
        "by": "tester",
    }, ensure_ascii=False), encoding="utf-8")

    stats = stats_from_answers(ws)
    assert stats["claims"] == 2
    assert stats["derived_ratio"] == 0.5
    assert stats["unsupported_ratio"] == 0.0


def test_review_metadata_survives_rebuild_without_churn(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    from course_content_organization.knowledge import load_units

    units_dir = ws / ".study" / "knowledge" / "units"
    units = load_units(units_dir)
    unit = next(u for u in units if split_claims(u))
    prepare(ws, units, {}, only=unit.id)
    claims = split_claims(unit)
    answers_dir = ws / ".study" / "gaps" / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    (answers_dir / f"{gap_id(unit.id)}.json").write_text(json.dumps({
        "verdicts": [{"claim_idx": claims[0]["claim_idx"], "verdict": "unsupported",
                      "quote": claims[0]["text"][:10], "reason": "证据中没有"}],
        "by": "tester",
    }, ensure_ascii=False), encoding="utf-8")

    main(["--workspace", str(ws), "review", "apply"])
    first = {u.id: u for u in load_units(units_dir)}[unit.id]
    assert first.origin == "synthesized"

    main(["--workspace", str(ws), "build"])
    second = {u.id: u for u in load_units(units_dir)}[unit.id]
    assert second.origin == "synthesized", "a rebuild must not erase review verdicts"
    assert "l2" in second.reviewed_by
    assert second.revision == first.revision, "metadata-only rebuilds must not bump revision"

    main(["--workspace", str(ws), "build"])
    third = {u.id: u for u in load_units(units_dir)}[unit.id]
    assert third.revision == first.revision
