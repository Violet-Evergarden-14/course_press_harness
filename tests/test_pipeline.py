"""End-to-end properties promised by the design docs (§17.1 of architecture.md)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from study_harness.cli import main


def _workspace(tmp_path: Path, pptx: Path) -> Path:
    ws = tmp_path / "ws"
    assert main(["--workspace", str(ws), "init", "--course", "数据结构"]) == 0
    inputs = ws / "inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / pptx.name).write_bytes(pptx.read_bytes())
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"] = [{"number": 6, "title": "树和二叉树",
                                  "sources": [f"inputs/{pptx.name}"]}]
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ws


def test_build_without_model_produces_document(tmp_path: Path, sample_pptx: Path):
    ws = _workspace(tmp_path, sample_pptx)
    rc = main(["--workspace", str(ws), "build"])
    book = ws / ".study" / "out" / "book.md"
    assert book.exists(), "build must succeed with no model and no network"
    text = book.read_text(encoding="utf-8")
    assert "第 6 讲 树和二叉树" in text
    assert rc in (0, 1)


def test_no_source_references_in_body(tmp_path: Path, sample_pptx: Path):
    ws = _workspace(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    text = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    for word in ("课件", "PPT", "幻灯片", "老师", "课上", "录音"):
        assert word not in text, f"source reference leaked: {word}"


def test_render_is_byte_identical(tmp_path: Path, sample_pptx: Path):
    ws = _workspace(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    book = ws / ".study" / "out" / "book.md"
    first = book.read_bytes()
    main(["--workspace", str(ws), "render"])
    assert book.read_bytes() == first


def test_appending_a_lecture_keeps_earlier_text(tmp_path: Path, sample_pptx: Path):
    ws = _workspace(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    book = ws / ".study" / "out" / "book.md"
    before = book.read_text(encoding="utf-8")

    # add a second lecture
    import shutil

    second = ws / "inputs" / "第07章+图.pptx"
    shutil.copyfile(sample_pptx, second)
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"].append({"number": 7, "title": "图",
                                      "sources": [f"inputs/{second.name}"]})
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    main(["--workspace", str(ws), "build"])
    after = book.read_text(encoding="utf-8")

    # the TOC legitimately changes; the body of lecture 6 must not
    def body(text: str) -> str:
        marker = "\n# 第 6 讲"
        return text[text.index(marker):] if marker in text else text

    assert body(before) in body(after), "appending a lecture must not rewrite earlier text"


def test_sections_follow_deck_numbering(tmp_path: Path, sample_pptx: Path):
    ws = _workspace(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    text = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "## 6.1 树的定义和基本术语" in text
    assert "## 6.2 二叉树" in text


def test_outline_is_lecture_ordered(tmp_path: Path, sample_pptx: Path):
    ws = _workspace(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    outline = json.loads((ws / ".study" / "outline.json").read_text(encoding="utf-8"))
    kinds = [p["kind"] for p in outline["parts"]]
    assert kinds[0] == "front_matter"
    assert "lecture" in kinds
    nos = [p["no"] for p in outline["parts"] if p["kind"] == "lecture"]
    assert nos == sorted(nos)


def test_gates_flag_auto_ids_only_as_major(tmp_path: Path, sample_pptx: Path):
    ws = _workspace(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    findings = [json.loads(line) for line in
                (ws / ".study" / "review" / "findings.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
    auto = [f for f in findings if f["gate"] == "auto_unit_id"]
    assert auto and auto[0]["level"] == "major"
    assert not [f for f in findings if f["level"] == "blocker"]
