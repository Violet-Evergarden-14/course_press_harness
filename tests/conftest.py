from __future__ import annotations

import sys
from pathlib import Path

import pytest

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

    slide("第06章 树和二叉树", ["课程封面", "学习重点"])
    slide("6.1 树的定义和基本术语", ["树是 n 个结点的有限集", "n=0 时为空树", "结点的度指子树个数"])
    slide("6.1 树的定义和基本术语", ["森林是 m 棵互不相交的树的集合"])
    slide("6.2 二叉树", ["二叉树每个结点至多两棵子树", "第 i 层至多有 2^(i-1) 个结点"])
    slide("6.2.1 二叉树的性质", ["满二叉树与完全二叉树是两种特殊形态"])

    out = tmp_path / "第06章+树和二叉树.pptx"
    prs.save(str(out))
    return out
