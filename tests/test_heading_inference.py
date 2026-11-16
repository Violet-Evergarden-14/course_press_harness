"""Decks whose headings are plain sentences must still yield multiple units.

Real course decks often have no numbered headings and no title placeholder: the
heading is simply the largest text on the slide. Before heading inference, a 100-slide
deck collapsed into a single unit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from course_content_organization.cli import main
from course_content_organization.ingest import adapter_for


@pytest.fixture
def sentence_deck(tmp_path: Path) -> Path:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    blank = prs.slide_layouts[6]

    def slide(heading: str, *lines: str, size: int = 28) -> None:
        s = prs.slides.add_slide(blank)
        box = s.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(8), Inches(0.9))
        run = box.text_frame.paragraphs[0].add_run()
        run.text = heading
        run.font.size = Pt(size)
        body = s.shapes.add_textbox(Inches(0.5), Inches(1.6), Inches(8), Inches(4))
        body.text_frame.text = lines[0] if lines else ""
        for line in lines[1:]:
            body.text_frame.add_paragraph().text = line

    slide("摩尔定律", "集成电路上的晶体管数量每两年翻一番")
    slide("摩尔定律", "这解释了为什么硬件性能持续增长")
    slide("类C语言", "用 C 的语法描述算法")
    slide("类C语言", "保留 C 的控制结构")
    slide("内存泄露", "malloc 之后忘记 free 会持续占用内存")
    slide("内存泄露", "这是笔试常见的扣分点")

    out = tmp_path / "第01章+绪论.pptx"
    prs.save(str(out))
    return out


def _ws(tmp_path: Path, pptx: Path) -> Path:
    ws = tmp_path / "ws"
    main(["--workspace", str(ws), "init", "--course", "示例课程"])
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / "inputs" / pptx.name).write_bytes(pptx.read_bytes())
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"] = [{"number": 1, "title": "绪论", "sources": [f"inputs/{pptx.name}"]}]
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ws


def test_large_font_text_becomes_a_heading(sentence_deck: Path, tmp_path: Path):
    ctx = type("Ctx", (), {"assets_dir": tmp_path / "assets", "config": None})()
    result = adapter_for(sentence_deck)().ingest(sentence_deck, ctx)
    titles = [b.text for b in result.blocks if b.type == "title"]
    assert "摩尔定律" in titles
    assert "类C语言" in titles


def test_sentence_headed_deck_is_not_one_unit(sentence_deck: Path, tmp_path: Path):
    ws = _ws(tmp_path, sentence_deck)
    main(["--workspace", str(ws), "build"])
    units = list((ws / ".study" / "knowledge" / "units").glob("*.md"))
    assert len(units) >= 3, f"expected several units, got {len(units)}"


def test_running_header_is_not_taken_for_a_heading(tmp_path: Path):
    from pptx import Presentation
    from pptx.util import Inches, Pt

    prs = Presentation()
    blank = prs.slide_layouts[6]
    for i in range(8):
        s = prs.slides.add_slide(blank)
        head = s.shapes.add_textbox(Inches(0.5), Inches(0.1), Inches(8), Inches(0.6))
        r = head.text_frame.paragraphs[0].add_run()
        r.text = "第1章 绪论"
        r.font.size = Pt(30)
        body = s.shapes.add_textbox(Inches(0.5), Inches(1.5), Inches(8), Inches(3))
        body.text_frame.text = f"正文第 {i} 页的内容说明"

    path = tmp_path / "第01章+绪论.pptx"
    prs.save(str(path))

    ctx = type("Ctx", (), {"assets_dir": tmp_path / "assets", "config": None})()
    result = adapter_for(path)().ingest(path, ctx)
    titles = [b.text for b in result.blocks if b.type == "title"]
    assert "第1章 绪论" not in titles, "a repeated running header must not become each slide's heading"
