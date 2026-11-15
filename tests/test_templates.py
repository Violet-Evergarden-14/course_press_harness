"""Layout is data: a different manifest changes the document without touching code."""

from __future__ import annotations

import yaml

from study_harness.cli import main


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


def test_manifest_controls_headings(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    tdir = ws / "templates" / "learn" / "md"
    tdir.mkdir(parents=True)
    (tdir / "manifest.yaml").write_text(
        "section:\n  heading: '## 【{index}】{title}'\n"
        "unit:\n  heading: '### {number}｜{title}'\n  section_label: '>> {name}'\n",
        encoding="utf-8")
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["render"]["template"] = "templates/learn/md"
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    main(["--workspace", str(ws), "render"])
    book = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "## 【1】树的定义和基本术语" in book
    assert ">> 定义" in book


def test_section_order_is_data(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    tdir = ws / "templates" / "learn" / "md"
    tdir.mkdir(parents=True)
    (tdir / "manifest.yaml").write_text(
        "section_order:\n  learn: [说明, 定义]\n", encoding="utf-8")
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["render"]["template"] = "templates/learn/md"
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    from study_harness.knowledge import load_units
    from study_harness.render import _ordered_sections
    from study_harness.templates import TemplateSet

    templates = TemplateSet.load(ws / "templates" / "learn" / "md")
    units = load_units(ws / ".study" / "knowledge" / "units")
    with_both = [u for u in units if {"定义", "说明"} <= set(u.sections)]
    assert with_both, "fixture should produce a unit with both sections"
    order = [name for name, _ in _ordered_sections(with_both[0], "learn", templates)]
    assert order[:2] == ["说明", "定义"], f"section order not applied: {order}"
