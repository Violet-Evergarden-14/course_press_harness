"""PDF layout heuristics, tested on synthetic line data (no binary fixture needed)."""

from __future__ import annotations

from course_content_organization.ingest.pdf_adapter import PdfAdapter


def _line(top: float, x0: float, x1: float, text: str, size: float = 10.5):
    return {
        "words": [], "text": text, "top": top, "x0": x0,
        "bbox": [x0, top, x1, top + 12], "size": size,
        "page_max_size": 16.0, "body_size": 10.5,
    }


def test_two_column_page_is_read_column_by_column():
    adapter = PdfAdapter()
    left = [_line(10 + i * 20, 40, 260, f"L{i}") for i in range(5)]
    right = [_line(10 + i * 20, 320, 560, f"R{i}") for i in range(5)]
    ordered = adapter._order_columns(left + right, page_width=600)
    assert [ln["text"] for ln in ordered] == ["L0", "L1", "L2", "L3", "L4",
                                              "R0", "R1", "R2", "R3", "R4"]


def test_single_column_is_left_untouched():
    adapter = PdfAdapter()
    lines = [_line(10 + i * 20, 40, 560, f"L{i}") for i in range(6)]
    assert adapter._order_columns(lines, page_width=600) == lines


def test_repeated_header_and_footer_are_furniture():
    adapter = PdfAdapter()
    pages = []
    for i in range(6):
        pages.append([_line(5, 40, 560, "数据结构与算法 I"),
                      _line(30, 40, 560, f"正文 {i}"),
                      _line(800, 40, 560, "第 3 页")])
    furniture = adapter._furniture(pages)
    assert "数据结构与算法 I" in furniture
    assert "第 3 页" in furniture
    assert "正文 0" not in furniture


def test_toc_page_with_dot_leaders_is_detected():
    adapter = PdfAdapter()
    lines = [_line(40, 40, 560, f"第{i}章 主题 {'......'} {i}") for i in range(1, 7)]
    assert adapter._is_toc_page(lines)


def test_body_page_is_not_a_toc():
    adapter = PdfAdapter()
    lines = [_line(40 + i * 20, 40, 560, f"这是一段连续正文的第 {i} 行，用来测试判定。")
             for i in range(6)]
    assert not adapter._is_toc_page(lines)
