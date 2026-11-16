"""The model boundary: gaps are files, answers are files, nobody calls an API."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from course_content_organization.cli import main
from course_content_organization.gaps import apply_answers, fill_gap, load_answers, open_gaps, register_image_gaps


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
    return ws


def test_build_registers_a_gap_per_image(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    pending = open_gaps(ws)
    assert pending, "images without semantics must produce gaps, not silent omissions"
    spec = json.loads(pending[0].read_text(encoding="utf-8"))
    assert spec["kind"] in ("image_semantics", "page_text")
    assert spec["question"] and "context" in spec


def test_answer_requires_declared_fields(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    gap_id = open_gaps(ws)[0].stem
    try:
        fill_gap(ws, gap_id, {"nonsense_field": "x"})
    except ValueError as exc:
        assert "未声明字段" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("fill_gap should reject undeclared fields")


def test_answer_flows_into_the_document(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    gap_id = open_gaps(ws)[0].stem
    fill_gap(ws, gap_id, {"summary": "一棵三元素的示例结构示意", "relations": [["根", "左子结构"]],
                          "by": "tester"})
    main(["--workspace", str(ws), "build"])
    book = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "一棵三元素的示例结构示意" in book
    assert "根→左子结构" in book


def test_answers_are_cached_not_rewritten(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    gap_id = open_gaps(ws)[0].stem
    path = fill_gap(ws, gap_id, {"summary": "已填写"})
    before = path.read_bytes()
    main(["--workspace", str(ws), "build"])
    assert path.read_bytes() == before, "a rebuild must not overwrite a filled answer"


def test_apply_answers_marks_the_source(tmp_path):
    blocks = [{"id": "s06-001-1", "type": "image", "asset": "assets/x.png", "loc": {"slide": 1}}]
    answers = {"img-s06-001-1": {"summary": "图意", "by": "claude-code/opus"}}
    assert apply_answers(blocks, answers) == 1
    assert blocks[0]["semantics"] == "图意"
    assert blocks[0]["semantics_by"] == "claude-code/opus"
