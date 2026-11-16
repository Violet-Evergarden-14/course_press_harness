from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

_ONE_PX_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture
def sample_pptx(tmp_path: Path) -> Path:
    """A tiny deck built from scratch: section heading, body, formula-ish text."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    layout = prs.slide_layouts[5]  # title only

    def slide(title: str, bullets: list[str]) -> None:
        s = prs.slides.add_slide(layout)
        s.shapes.title.text = title
        if bullets:
            box = s.shapes.add_textbox(Inches(0.5), Inches(1.6), Inches(8), Inches(4))
            tf = box.text_frame
            tf.text = bullets[0]
            for b in bullets[1:]:
                p = tf.add_paragraph()
                p.text = b

    slide("第6章 示例专题", ["课程封面", "学习重点"])
    slide("6.1 树的定义和基本术语", ["树是 n 个结点的有限集", "n=0 时为空树", "结点的度指子树个数"])
    slide("6.1 树的定义和基本术语", ["森林是 m 棵互不相交的树的集合"])
    slide("6.2 二叉树", ["二叉树每个结点至多两棵子树", "第 i 层至多有 2^(i-1) 个结点"])
    slide("6.2.1 二叉树的性质", ["满二叉树与完全二叉树是两种特殊形态"])

    # one picture, so image gaps have something to attach to
    png = tmp_path / "figure.png"
    png.write_bytes(base64.b64decode(_ONE_PX_PNG))
    prs.slides[1].shapes.add_picture(str(png), Inches(0.5), Inches(5))

    out = tmp_path / "第6章+示例专题.pptx"
    prs.save(str(out))
    return out


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    """A Word handout: heading styles, a list, a table and one inline image."""
    from docx import Document
    from docx.shared import Inches

    doc = Document()
    doc.add_heading("第6章 示例专题", level=1)
    doc.add_paragraph("本章讨论树与二叉树的基本概念与遍历。")

    doc.add_heading("6.1 树的定义", level=2)
    doc.add_paragraph("树是 n 个结点的有限集。", style="List Bullet")
    doc.add_paragraph("n=0 时为空树。", style="List Bullet")

    doc.add_heading("6.2 二叉树", level=2)
    doc.add_paragraph("二叉树每个结点至多有两棵子树。")

    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "形态"
    table.cell(0, 1).text = "结点数"
    table.cell(1, 0).text = "满二叉树"
    table.cell(1, 1).text = "2^h-1"

    png = tmp_path / "doc.png"
    png.write_bytes(base64.b64decode(_ONE_PX_PNG))
    doc.add_picture(str(png), width=Inches(0.5))

    out = tmp_path / "第6章+示例专题.docx"
    doc.save(str(out))
    return out
