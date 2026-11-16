"""Word adapter: heading styles, lists, tables, inline images (docs/ingest.md §6)."""

from __future__ import annotations

from pathlib import Path

import yaml

from course_content_organization.cli import main
from course_content_organization.ingest import adapter_for
from course_content_organization.ingest.docx_adapter import DocxAdapter


def _ws(tmp_path: Path, docx_path: Path) -> Path:
    ws = tmp_path / "ws"
    main(["--workspace", str(ws), "init", "--course", "数据结构"])
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / "inputs" / docx_path.name).write_bytes(docx_path.read_bytes())
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"] = [{"number": 6, "title": "树和二叉树",
                                  "sources": [f"inputs/{docx_path.name}"]}]
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ws


def test_docx_is_dispatched(tmp_path: Path, sample_docx: Path):
    assert adapter_for(sample_docx) is DocxAdapter


def test_heading_styles_become_title_levels(sample_docx: Path, tmp_path: Path):
    ctx = type("Ctx", (), {"assets_dir": tmp_path / "assets", "config": None})()
    result = DocxAdapter().ingest(sample_docx, ctx)
    titles = [(b.level, b.text) for b in result.blocks if b.type == "title"]
    assert (1, "第06章 树和二叉树") in titles
    assert (2, "6.1 树的定义") in titles
    assert (2, "6.2 二叉树") in titles


def test_tables_and_images_are_extracted(sample_docx: Path, tmp_path: Path):
    ctx = type("Ctx", (), {"assets_dir": tmp_path / "assets", "config": None})()
    result = DocxAdapter().ingest(sample_docx, ctx)
    tables = [b for b in result.blocks if b.type == "table"]
    assert tables and tables[0].rows[0] == ["形态", "结点数"]
    images = [b for b in result.blocks if b.type == "image"]
    assert images and images[0].asset.startswith("assets/")
    assert (tmp_path / "assets" / Path(images[0].asset).name).exists()


def test_locators_are_paragraph_indices(sample_docx: Path, tmp_path: Path):
    ctx = type("Ctx", (), {"assets_dir": tmp_path / "assets", "config": None})()
    result = DocxAdapter().ingest(sample_docx, ctx)
    ids = [b.id for b in result.blocks]
    assert len(ids) == len(set(ids)), "block ids must be unique"
    assert all(b.loc for b in result.blocks)


def test_build_from_docx_produces_a_document(tmp_path: Path, sample_docx: Path):
    ws = _ws(tmp_path, sample_docx)
    main(["--workspace", str(ws), "build"])
    book = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "树和二叉树" in book
    assert "满二叉树" in book
    assert "课件" not in book and "Word" not in book
