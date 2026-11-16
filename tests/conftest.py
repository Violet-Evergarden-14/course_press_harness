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

    # neutral placeholder content: this fixture must not resemble any real course
    slide("第 6 讲 示例主题 A", ["示例封面", "阅读提示"])
    slide("6.1 示例小节一", ["示例条目甲", "示例条目乙", "示例条目丙"])
    slide("6.1 示例小节一", ["示例条目丁"])
    slide("6.2 示例小节二", ["示例条目戊", "示例条目己"])
    slide("6.2.1 示例小节二的细节", ["示例条目庚"])

    # one picture, so image gaps have something to attach to
    png = tmp_path / "figure.png"
    png.write_bytes(base64.b64decode(_ONE_PX_PNG))
    prs.slides[1].shapes.add_picture(str(png), Inches(0.5), Inches(5))

    out = tmp_path / "第06讲+示例主题.pptx"
    prs.save(str(out))
    return out


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    """A Word handout: heading styles, a list, a table and one inline image."""
    from docx import Document
    from docx.shared import Inches

    doc = Document()
    doc.add_heading("第 6 讲 示例主题 A", level=1)
    doc.add_paragraph("这是用于测试的占位文档。")

    doc.add_heading("6.1 示例小节一", level=2)
    doc.add_paragraph("示例条目甲。", style="List Bullet")
    doc.add_paragraph("示例条目乙。", style="List Bullet")

    doc.add_heading("6.2 示例小节二", level=2)
    doc.add_paragraph("示例条目戊。")

    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "类别"
    table.cell(0, 1).text = "数量"
    table.cell(1, 0).text = "示例类别一"
    table.cell(1, 1).text = "3"

    png = tmp_path / "doc.png"
    png.write_bytes(base64.b64decode(_ONE_PX_PNG))
    doc.add_picture(str(png), width=Inches(0.5))

    out = tmp_path / "第6章+示例主题.docx"
    doc.save(str(out))
    return out
