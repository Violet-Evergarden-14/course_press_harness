"""L3 adversarial review and on-demand explanation patches."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from course_content_organization import remediate
from course_content_organization.cli import main
from course_content_organization.knowledge import load_units
from course_content_organization.review import apply_l3, l3_gap_id, prepare_l3


def _ws(tmp_path: Path, pptx: Path) -> Path:
    ws = tmp_path / "ws"
    main(["--workspace", str(ws), "init", "--course", "数据结构"])
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / "inputs" / pptx.name).write_bytes(pptx.read_bytes())
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"] = [{"number": 6, "title": "树和二叉树",
                                  "sources": [f"inputs/{pptx.name}"]}]
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    main(["--workspace", str(ws), "build"])
    return ws


def test_l3_gap_carries_isolation_rules(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    units = load_units(ws / ".study" / "knowledge" / "units")
    created = prepare_l3(ws, units, sample_ratio=0.0)
    assert created, "units with formulas/examples or synthesized origin must be reviewed"
    spec = json.loads(created[0].read_text(encoding="utf-8"))
    assert spec["kind"] == "adversarial_review"
    assert spec["isolation"]["prefer_different_model"] is True
    assert len(spec["questions"]) == 3
    assert any("不重写" in r for r in spec["rules"])


def test_l3_happy_path_returns_no_issues(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    units = load_units(ws / ".study" / "knowledge" / "units")
    prepare_l3(ws, units, only=units[0].id, sample_ratio=0.0)
    answers = ws / ".study" / "gaps" / "answers"
    answers.mkdir(parents=True, exist_ok=True)
    (answers / f"{l3_gap_id(units[0].id)}.json").write_text(json.dumps({
        "issues": [], "by": "other-model", "confidence": "high"}, ensure_ascii=False),
        encoding="utf-8")
    findings, stats = apply_l3(ws, ws / ".study" / "knowledge" / "units",
                              load_units(ws / ".study" / "knowledge" / "units"))
    assert stats["units_reviewed"] == 1 and stats["issues"] == 0
    assert findings == []


def test_l3_issues_become_categorised_findings(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    units = load_units(ws / ".study" / "knowledge" / "units")
    unit = units[0]
    prepare_l3(ws, units, only=unit.id, sample_ratio=0.0)
    answers = ws / ".study" / "gaps" / "answers"
    answers.mkdir(parents=True, exist_ok=True)
    (answers / f"{l3_gap_id(unit.id)}.json").write_text(json.dumps({
        "issues": [
            {"question": 1, "severity": "blocker", "message": "与另一节关于层数的说法相反"},
            {"question": 2, "severity": "major", "message": "少一步归纳假设"},
            {"question": 3, "severity": "minor", "message": "例子算出来是 31 不是 32"},
            {"question": 9, "severity": "nonsense", "message": "无法归类的回答"},
        ],
        "by": "other-model", "confidence": "medium"}, ensure_ascii=False), encoding="utf-8")

    findings, stats = apply_l3(ws, ws / ".study" / "knowledge" / "units",
                              load_units(ws / ".study" / "knowledge" / "units"))
    gates = {f["gate"] for f in findings}
    assert {"contradiction", "missing_step", "numeric_mismatch"} <= gates
    assert stats["issues"] == 4
    assert {f["level"] for f in findings} <= {"blocker", "major", "minor", "info"}


def test_explain_patch_is_overlaid_not_merged(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    units = load_units(ws / ".study" / "knowledge" / "units")
    unit = units[0]

    main(["--workspace", str(ws), "explain", unit.id, "--focus", "为什么要 n≥0"])
    gap = ws / ".study" / "gaps" / f"{remediate.gap_id(unit.id)}.json"
    assert gap.exists()
    spec = json.loads(gap.read_text(encoding="utf-8"))
    assert spec["kind"] == "remedial" and "origin: synthesized" in " ".join(spec["rules"])

    answers = ws / ".study" / "gaps" / "answers"
    answers.mkdir(parents=True, exist_ok=True)
    (answers / f"{remediate.gap_id(unit.id)}.json").write_text(
        json.dumps({"markdown": "空树是一个合法的树：$n=0$ 时没有根也没有子树。",
                    "by": "tester"}, ensure_ascii=False), encoding="utf-8")

    main(["--workspace", str(ws), "explain", "--apply"])
    patch = ws / ".study" / "patches" / "remedial" / f"{unit.id}.md"
    assert patch.exists()

    unit_file = ws / ".study" / "knowledge" / "units" / f"{unit.id}.md"
    before = unit_file.read_text(encoding="utf-8")
    assert "空树是一个合法的树" not in before, "remarks must not be merged into the unit"

    main(["--workspace", str(ws), "render"])
    book = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "补充说明" in book and "空树是一个合法的树" in book

    main(["--workspace", str(ws), "explain", "--remove", unit.id])
    main(["--workspace", str(ws), "render"])
    book = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "空树是一个合法的树" not in book, "removing the patch restores the original text"
