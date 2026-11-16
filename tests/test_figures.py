"""Figures: extracted vs redrawn vs synthesized (docs/rendering.md §9)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from course_content_organization.cli import main
from course_content_organization.figures import add_figure, load_figures, request_figure


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


def test_mermaid_figure_renders_as_fence(tmp_path: Path, sample_pptx: Path):
    ws = _ws(tmp_path, sample_pptx)
    spec = tmp_path / "shape.mmd"
    spec.write_text("graph TD\n  A[根] --> B[子结构]\n", encoding="utf-8")
    add_figure(ws, "tree-shape", spec, provenance="synthesized", caption="示例结构形态")

    units = sorted((ws / ".study" / "knowledge" / "units").glob("*.md"))
    text = units[0].read_text(encoding="utf-8").rstrip() + "\n\n[[fig:tree-shape]]\n"
    units[0].write_text(text, encoding="utf-8")

    main(["--workspace", str(ws), "render"])
    book = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "```mermaid" in book, "a redrawn figure must ship as a diffable fence"
    assert "book.assets/tree-shape" not in book, "vector text must not be copied as a binary asset"


def test_missing_figure_is_a_blocker(tmp_path: Path, sample_pptx: Path):
    ws = _ws(tmp_path, sample_pptx)
    units = sorted((ws / ".study" / "knowledge" / "units").glob("*.md"))
    units[0].write_text(units[0].read_text(encoding="utf-8").rstrip() + "\n\n[[fig:nope]]\n",
                        encoding="utf-8")
    rc = main(["--workspace", str(ws), "render"])
    findings = [json.loads(l) for l in
                (ws / ".study" / "review" / "findings.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()]
    assert rc == 1
    assert any(f["gate"] == "figure_refs" and f["level"] == "blocker" for f in findings)


def test_bitmap_is_only_allowed_for_extracted_figures(tmp_path: Path, sample_pptx: Path):
    ws = _ws(tmp_path, sample_pptx)
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    add_figure(ws, "copy", png, provenance="redrawn")

    units = sorted((ws / ".study" / "knowledge" / "units").glob("*.md"))
    units[0].write_text(units[0].read_text(encoding="utf-8").rstrip() + "\n\n[[fig:copy]]\n",
                        encoding="utf-8")
    main(["--workspace", str(ws), "render"])
    findings = [json.loads(l) for l in
                (ws / ".study" / "review" / "findings.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()]
    assert any(f["gate"] == "figure_provenance" for f in findings)


def test_figure_request_writes_a_gap(tmp_path: Path):
    ws = tmp_path / "ws"
    main(["--workspace", str(ws), "init", "--course", "x"])
    path = request_figure(ws, "tree-def", "画出示例结构的五种基本形态", preferred="mermaid")
    gap = json.loads(path.read_text(encoding="utf-8"))
    assert gap["kind"] == "figure_request"
    assert gap["answer_format"]["format"] == "mermaid"
    assert any("文本矢量" in r for r in gap["rules"])


def test_semantic_id_survives_rebuild(tmp_path: Path, sample_pptx: Path):
    """Renaming a draft id and opting out of draft mode must survive `build`."""
    ws = _ws(tmp_path, sample_pptx)
    units_dir = ws / ".study" / "knowledge" / "units"
    first = sorted(units_dir.glob("*.md"))[0]
    text = first.read_text(encoding="utf-8")
    old_id = text.split("id: ", 1)[1].splitlines()[0].strip()

    head, body = text.split("\n---\n", 1)
    head = head.replace(f"id: {old_id}", "id: tree-def")
    head = head.replace("draft: true\n", "") + f"\naliases: [{old_id}]"
    body = body.replace("## 定义", "## 定义\n\n树是 n 个元素的有限集。", 1)
    first.unlink()
    (units_dir / "tree-def.md").write_text(head + "\n---\n" + body, encoding="utf-8")

    main(["--workspace", str(ws), "build"])
    files = {p.name for p in units_dir.glob("*.md")}
    assert "tree-def.md" in files
    assert f"{old_id}.md" not in files, "the old machine id must not be resurrected"
    assert "树是 n 个元素的有限集。" in (units_dir / "tree-def.md").read_text(encoding="utf-8")
