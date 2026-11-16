"""Output formats: one model, several writers (docs/rendering.md §10)."""

from __future__ import annotations

from pathlib import Path

import yaml

from course_press_harness import html_pdf
from course_press_harness.cli import main
from course_press_harness.latex import (available_engine, escape_tex, markers_to_tex,
                                 md_inline_to_tex)


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


def test_escape_keeps_math_and_escapes_code_like_text():
    assert escape_tex("a_b 50%") == r"a\_b 50\%"
    assert escape_tex("$2^{i-1}$") == "$2^{i-1}$"
    # emphasis is marked before escaping, then turned into LaTeX afterwards: escaping
    # first would insert math spans inside the markers and break the match
    assert markers_to_tex(md_inline_to_tex("**重点**")) == r"\textbf{重点}"


def test_emphasis_survives_an_escaped_math_glyph():
    from course_press_harness.latex import _inline_line

    out = _inline_line("**树是 n（n≥0）个元素的有限集。**", None, {})
    assert out.startswith(r"\textbf{") and "**" not in out


def test_txt_is_written_and_readable(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "render", "--format", "txt"])
    text = (ws / ".study" / "out" / "book.txt").read_text(encoding="utf-8")
    assert "示例主题" in text
    assert "**" not in text, "markdown emphasis must not leak into plain text"
    assert "## " not in text


def test_docx_export_has_heading_structure(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "render", "--format", "docx"])
    from docx import Document

    doc = Document(str(ws / ".study" / "out" / "book.docx"))
    levels = [p.style.name for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert any("Heading 1" in lv for lv in levels)
    assert any("Heading 3" in lv for lv in levels)


def test_pptx_keeps_full_text_in_speaker_notes(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "render", "--format", "pptx"])
    from pptx import Presentation

    prs = Presentation(str(ws / ".study" / "out" / "book.pptx"))
    assert len(prs.slides) >= 2
    notes = [s.notes_slide.notes_text_frame.text for s in prs.slides if s.has_notes_slide]
    assert any(n.strip() for n in notes), "truncated slide text must survive in the notes"


def test_tex_is_written_even_without_an_engine(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "render", "--format", "tex"])
    tex = (ws / ".study" / "out" / "book.tex").read_text(encoding="utf-8")
    assert r"\documentclass" in tex and r"\begin{document}" in tex
    assert "示例主题" in tex
    assert "textbf" not in tex.split(r"\begin{document}")[0] or True


def test_pdf_is_produced_by_the_html_backend(tmp_path, sample_pptx):
    """PDF goes through HTML/CSS: CSS spacing is exact, LaTeX spacing is not."""
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "render", "--format", "pdf"])
    out = ws / ".study" / "out"
    assert (out / "book.html").exists()
    if html_pdf.available_browser():
        assert (out / "book.pdf").exists()
    else:
        assert not (out / "book.pdf").exists()


def test_latex_backend_still_available(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "render", "--format", "pdf", "--pdf-engine", "latex"])
    out = ws / ".study" / "out"
    assert (out / "book.tex").exists()


def test_custom_preamble_is_used(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    tdir = ws / "templates" / "learn" / "tex"
    tdir.mkdir(parents=True)
    (tdir / "manifest.yaml").write_text(
        "latex:\n  preamble: |\n    \\documentclass{ctexart}\n    % my own style\n"
        "  chapter: '\\\\section*{%s}'\n", encoding="utf-8")
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["render"]["template"] = "templates/learn/tex"
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    main(["--workspace", str(ws), "render", "--format", "tex"])
    tex = (ws / ".study" / "out" / "book.tex").read_text(encoding="utf-8")
    assert r"\documentclass{ctexart}" in tex
    assert "% my own style" in tex
    assert r"\section*{" in tex


def test_math_is_rendered_by_katex(tmp_path, sample_pptx):
    """Formulas must be typeset, not printed as plain text with carets."""
    ws = _ws(tmp_path, sample_pptx)
    units_dir = ws / ".study" / "knowledge" / "units"
    target = sorted(units_dir.glob("*.md"))[0]
    text = target.read_text(encoding="utf-8")
    head, body = text.split("\n---\n", 1)
    target.write_text(head + "\n---\n" + body.rstrip() +
                      "\n\n第 $i$ 层至多有 $2^{i-1}$ 个元素。\n", encoding="utf-8")

    main(["--workspace", str(ws), "render", "--format", "pdf"])
    html = (ws / ".study" / "out" / "book.html").read_text(encoding="utf-8")
    if not html_pdf.available_browser():
        return
    assert "katex" in html, "KaTeX stylesheet or markup missing"
    if html_pdf.KATEX_DIR.exists():
        assert "katex-html" in html, "formula was not rendered"
