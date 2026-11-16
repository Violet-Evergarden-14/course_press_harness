"""The review view: same units, different presentation (docs/rendering.md §4)."""

from __future__ import annotations

import yaml

from course_content_organization.cli import main


def _ws(tmp_path, pptx):
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


def test_formula_appendix_collects_formulas_once(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    units_dir = ws / ".study" / "knowledge" / "units"
    target = sorted(units_dir.glob("*.md"))[0]
    text = target.read_text(encoding="utf-8")
    head, body = text.split("\n---\n", 1)
    target.write_text(head + "\n---\n" + body.rstrip() +
                      "\n\n## 算式\n\n$$ 2^{i-1} $$\n", encoding="utf-8")

    main(["--workspace", str(ws), "render"])
    book = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "## 附录 B 公式速查" in book
    assert book.count("$$ 2^{i-1} $$") == 2, "formula appears inline and once in the appendix"


def test_review_profile_lists_emphasis(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    units_dir = ws / ".study" / "knowledge" / "units"
    target = sorted(units_dir.glob("*.md"))[0]
    text = target.read_text(encoding="utf-8")
    head, body = text.split("\n---\n", 1)
    if "emphasis:" not in head:
        head = head.replace("draft: true", "draft: true\nemphasis: true")
    target.write_text(head + "\n---\n" + body, encoding="utf-8")

    main(["--workspace", str(ws), "render", "--profile", "review"])
    book = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "## 附录 A 重点清单" in book

    # the learn view must not carry the exam-focus appendix
    main(["--workspace", str(ws), "render", "--profile", "learn"])
    book = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "## 附录 A 重点清单" not in book
