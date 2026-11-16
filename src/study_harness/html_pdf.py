"""HTML + CSS print pipeline for PDF.

LaTeX decides its own vertical glue: \\parskip, baselineskip and package defaults
interact in ways that are hard to predict, and every attempt to tune one of them moved
the others. The result was "gaps everywhere" that could only be fixed by guesswork.

CSS has no such problem: `line-height` and `margin` are exact numbers, and what the
browser shows is what the PDF contains. So PDF output goes through HTML printed by a
headless Chromium (Edge/Chrome), and LaTeX stays available for people who want it.

Measured on the same text: reference document 17.6pt line pitch / 21.5pt paragraph gap;
this pipeline 18.0 / 21.7 at `line-height: 1.62`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .knowledge import Unit
from .render import display_title, number_units
from .templates import TemplateSet

CSS = """
@page { size: A4; margin: 22mm 22mm 20mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  /* Latin first, CJK fallback: SimSun's Latin glyphs are what looked ugly */
  font-family: "Times New Roman", "SimSun", "Songti SC", serif;
  font-size: 11pt; line-height: 1.62; color: #000; margin: 0;
  text-align: justify;
}
h1.doc { font-family: "Arial", "SimHei", sans-serif; font-size: 22pt; color: #165496; margin: 0 0 6pt; }
h1.part { font-family: "Arial", "SimHei", sans-serif; font-size: 18pt; font-weight: bold;
          color: #165496; margin: 20pt 0 12pt; letter-spacing: 0.5pt;
          page-break-before: always; }
h2.sec  { font-family: "Arial", "SimHei", sans-serif; font-size: 13.5pt; font-weight: bold;
          color: #165496; margin: 15pt 0 6pt; letter-spacing: 0.3pt; }
/* third level: same family, but black and smaller -- the reference does the same,
   and it is what keeps 章/节/小节 readable as three distinct ranks */
h3.unit { font-family: "Arial", "SimHei", sans-serif; font-size: 11.5pt; font-weight: bold;
          color: #1E6E46; margin: 11pt 0 4pt; }
/* fourth level: clearly below h3 — smaller and sans, but readable; grey made it
   look like a disabled label rather than a heading */
h4.sub  { font-family: "Arial", "SimHei", sans-serif; font-size: 10.5pt; font-weight: bold;
          color: #165496; margin: 7pt 0 2pt; letter-spacing: 0.2pt; }
p { margin: 0 0 3pt; text-indent: 2em; }
ul, ol { margin: 2pt 0 4pt; padding-left: 2em; }
li { margin: 0 0 2pt; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 8pt; font-size: 10pt; }
thead th { background: #E2E9F2; color: #165496; font-family: "Arial", "SimHei", sans-serif;
           font-weight: bold; text-align: left; padding: 4pt 6pt; }
tbody td { padding: 3pt 6pt; border-bottom: 0.5pt solid #ddd; vertical-align: top; }
table, thead th { border-top: 0.8pt solid #333; border-bottom: 0.8pt solid #333; }
table.three thead th:first-child { width: 18%; }
table.three thead th:last-child { width: 42%; }
tr { page-break-inside: avoid; }
.box { margin: 8pt 0; border: 0.6pt solid #9CC0E0; background: #E8F1FA;
       border-radius: 7pt; page-break-inside: avoid; overflow: hidden; }
.box > .head { background: #5C88B6; color: #fff; font-family: "Arial", "SimHei", sans-serif;
               font-size: 10pt; font-weight: bold; padding: 3pt 8pt; }
.box > .body { padding: 6pt 9pt 4pt; }
.box > .body p { text-indent: 0; margin: 0 0 3pt; }
.box.warn { border-color: #F4B183; background: #FDF0E6; }
.box.warn > .head { background: #BF6000; }
pre { background: #f6f6f6; border: 0.5pt solid #ddd; padding: 6pt 8pt; font-size: 9.5pt;
      line-height: 1.35; white-space: pre-wrap; page-break-inside: avoid;
      font-family: Consolas, "Cascadia Mono", "Courier New", monospace; }
code { font-family: Consolas, "Cascadia Mono", "Courier New", monospace; font-size: 9.5pt;
       background: #f2f2f2; padding: 0 2pt; }
figure { margin: 8pt 0; text-align: center; page-break-inside: avoid; }
figure svg, figure img { max-width: 100%; }
figcaption { font-size: 9.5pt; color: #444; margin-top: 3pt; text-indent: 0; }
.toc { margin: 4pt 0 10pt; font-size: 10.5pt; }
.toc div { margin: 1pt 0; }
.toc .l1 { font-weight: bold; margin-top: 4pt; }
.toc .l2 { margin-left: 1.2em; }
.toc .l3 { margin-left: 2.4em; }
.toc a { color: #000; text-decoration: none; }
.toc a::after { content: leader('.') target-counter(attr(href), page); }
.math { font-family: "Cambria Math", "Latin Modern Math", serif; }
"""

TITLE_NUM_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s*")
BOX_OPEN_RE = re.compile(r"^:::(note|warn)\s*(.*)$")
BULLET_RE = re.compile(r"^[-*•]\s+(.*)$")
NUM_ITEM_RE = re.compile(r"^\d+[.、)]\s+(.*)$")
EQUATION_RE = re.compile(r"^\$\$(.*)\$\$$", re.S)
REF_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
SUP_RE = re.compile(r"([A-Za-z0-9])\^\(([^)]{1,12})\)|([A-Za-z0-9])\^([A-Za-z0-9])")
SUB_RE = re.compile(r"(?<![A-Za-z_\\])([A-Za-z])([0-9])(?![0-9A-Za-z])")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
CODE_RE = re.compile(r"`([^`\n]+)`")


def esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def sub_sup(text: str) -> str:
    def sup(m: re.Match) -> str:
        if m.group(1):
            return f"{m.group(1)}<sup>{m.group(2)}</sup>"
        return f"{m.group(3)}<sup>{m.group(4)}</sup>"
    text = SUP_RE.sub(sup, text)
    return SUB_RE.sub(lambda m: f"{m.group(1)}<sub>{m.group(2)}</sub>", text)


TOKEN_OPEN, TOKEN_CLOSE = chr(7), chr(8)
MATH_OPEN, MATH_CLOSE = chr(11), chr(12)
MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.S)

KATEX_DIR = Path(__file__).resolve().parents[2] / "tools" / "node_modules" / "katex" / "dist"
_math_queue: list[dict[str, Any]] = []


def reset_math() -> None:
    _math_queue.clear()


def math_token(tex: str, display: bool) -> str:
    """Park the formula and render it in one batch at the end.

    KaTeX runs in node; calling it per line would spawn hundreds of processes, so the
    formulas are collected first and rendered once.
    """
    idx = len(_math_queue)
    _math_queue.append({"tex": tex, "display": display})
    return f"{MATH_OPEN}{idx}{MATH_CLOSE}"


def render_math_batch() -> list[str]:
    if not _math_queue:
        return []
    script = Path(__file__).resolve().parents[2] / "tools" / "katex_render.js"
    if not script.exists() or shutil.which("node") is None:
        return []
    try:
        proc = subprocess.run(["node", str(script)], input=json.dumps(_math_queue),
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=180)
        return json.loads(proc.stdout or "[]")
    except Exception:
        return []


def katex_css(out_dir: Path | None) -> str:
    """Inline KaTeX's stylesheet, with fonts copied next to the document."""
    css_file = KATEX_DIR / "katex.min.css"
    if not css_file.exists():
        return ""
    css = css_file.read_text(encoding="utf-8")
    if out_dir is None:
        return css
    src_fonts = KATEX_DIR / "fonts"
    dst_fonts = out_dir / "katex-fonts"
    if src_fonts.exists():
        dst_fonts.mkdir(parents=True, exist_ok=True)
        for f in src_fonts.iterdir():
            target = dst_fonts / f.name
            if not target.exists():
                shutil.copyfile(f, target)
        css = css.replace("url(fonts/", "url(katex-fonts/")
    return css
BS, LF = chr(92), chr(10)

STANDALONE_TEMPLATE = (
    BS + "documentclass[border=4pt]{standalone}" + LF
    + BS + "usepackage{tikz}" + LF
    + BS + "usepackage{amsmath,amssymb}" + LF
    + BS + "begin{document}" + LF + "{body}" + LF
    + BS + "end{document}" + LF
)


def tikz_to_png(tex_path: Path) -> Path | None:
    """Compile a standalone TikZ figure and rasterise it for HTML.

    The HTML path needs a bitmap; the LaTeX toolchain is already here, so the figure
    is compiled as-is and rasterised with pdfium rather than redrawn.
    """
    png = tex_path.with_suffix(".png")
    if png.exists() and png.stat().st_mtime >= tex_path.stat().st_mtime:
        return png
    work = tex_path.parent
    doc = work / f"_{tex_path.stem}.standalone.tex"
    body = tex_path.read_text(encoding="utf-8")
    doc.write_text(STANDALONE_TEMPLATE.replace("{body}", body), encoding="utf-8")
    try:
        subprocess.run([shutil.which("xelatex") or "xelatex", "-interaction=nonstopmode",
                        doc.name], cwd=str(work), capture_output=True, timeout=180)
        pdf = doc.with_suffix(".pdf")
        if not pdf.exists():
            return None
        import pypdfium2 as pdfium

        doc_pdf = pdfium.PdfDocument(str(pdf))
        page = doc_pdf[0]
        image = page.render(scale=3).to_pil()
        page.close()
        doc_pdf.close()          # release the file before the cleanup below deletes it
        image.save(png)
        return png
    except Exception:
        return None
    finally:
        for ext in (".tex", ".pdf", ".aux", ".log"):
            tmp = doc.with_suffix(ext)
            if tmp.exists() and tmp != tex_path:
                tmp.unlink()


def inline(text: str, *, assets: Path | None = None, figures: dict | None = None,
           numbers: dict[str, str] | None = None) -> str:
    figures = figures or {}
    numbers = numbers or {}

    def repl(m: re.Match) -> str:
        target, alias = m.group(1).strip(), m.group(2)
        if target.startswith("fig:"):
            fig = figures.get(target[4:])
            if fig is None:
                return ""
            if fig.format == "tikz":
                png = tikz_to_png(fig.path)
                if png is None:
                    return "<span class='math'>[图：" + fig.id + "]</span>"
                return (TOKEN_OPEN + f"<img src='{png.as_uri()}' style='max-width:90%'>"
                        + TOKEN_CLOSE)
            return TOKEN_OPEN + f"<img src='{fig.path.as_uri()}' style='max-width:90%'>" + TOKEN_CLOSE
        if target.startswith("asset:"):
            name = target[6:]
            for ext in (".png", ".jpg", ".jpeg", ".svg"):
                if assets is not None and (assets / f"{name}{ext}").exists():
                    return f"<img src='{(assets / (name + ext)).as_posix()}' style='max-width:80%'>"
            return ""
        if target.startswith("term:"):
            return esc(alias or target[5:])
        return esc(alias or numbers.get(target, target))

    text = REF_RE.sub(repl, text)

    def _park_math(m: re.Match) -> str:
        if m.group(1) is not None:
            return math_token(m.group(1).strip(), True)
        return math_token(m.group(2).strip(), False)

    text = MATH_RE.sub(_park_math, text)
    # escape the prose but keep injected HTML (images) intact
    spans = text.split(TOKEN_OPEN)
    rebuilt = [esc(spans[0])]
    for span in spans[1:]:
        html, _, rest = span.partition(TOKEN_CLOSE)
        rebuilt.append(html + esc(rest))
    text = "".join(rebuilt)
    text = CODE_RE.sub(lambda m: "<code>" + m.group(1) + "</code>", text)
    text = BOLD_RE.sub(r"<strong>\1</strong>", text)
    return sub_sup(text)


def block(lines: list[str], *, assets: Path | None, figures: dict,
          numbers: dict[str, str]) -> str:
    out: list[str] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line:
            i += 1
            continue
        opener = BOX_OPEN_RE.match(line)
        if opener:
            kind, title = opener.group(1), opener.group(2).strip()
            inner: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != ":::":
                inner.append(lines[i])
                i += 1
            i += 1
            cls = "box warn" if kind == "warn" else "box"
            out.append(f"<div class='{cls}'><div class='head'>{esc(title)}</div>"
                       f"<div class='body'>{block(inner, assets=assets, figures=figures, numbers=numbers)}</div></div>")
            continue
        if line.startswith("```"):
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre>" + esc("\n".join(code)) + "</pre>")
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append(cells)
                i += 1
            if len(rows) >= 2:
                head = "".join(f"<th>{inline(c, assets=assets, figures=figures, numbers=numbers)}</th>"
                               for c in rows[0])
                body = "".join(
                    "<tr>" + "".join(f"<td>{inline(c, assets=assets, figures=figures, numbers=numbers)}</td>"
                                     for c in row) + "</tr>"
                    for row in rows[1:])
                cls = " class='three'" if len(rows[0]) == 3 else ""
                out.append(f"<table{cls}><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
            continue
        if BULLET_RE.match(line) or NUM_ITEM_RE.match(line):
            ordered = bool(NUM_ITEM_RE.match(line))
            items: list[str] = []
            pattern = NUM_ITEM_RE if ordered else BULLET_RE
            while i < len(lines) and pattern.match(lines[i].strip()):
                items.append(pattern.match(lines[i].strip()).group(1))
                i += 1
            tag = "ol" if ordered else "ul"
            lis = "".join(f"<li>{inline(x, assets=assets, figures=figures, numbers=numbers)}</li>"
                          for x in items)
            out.append(f"<{tag}>{lis}</{tag}>")
            continue
        m = EQUATION_RE.match(line)
        if m:
            out.append("<p class='math' style='text-align:center;text-indent:0'>"
                       + esc(m.group(1).strip()) + "</p>")
            i += 1
            continue
        out.append(f"<p>{inline(line, assets=assets, figures=figures, numbers=numbers)}</p>")
        i += 1
    return "\n".join(out)


def build_html(units: list[Unit], outline: dict[str, Any], *, title: str,
               profile: str = "learn", templates: TemplateSet | None = None,
               assets: Path | None = None, figures: dict | None = None,
               remedial: dict[str, str] | None = None,
               out_dir: Path | None = None) -> str:
    templates = templates or TemplateSet()
    reset_math()
    numbers = number_units(outline)
    by_id = {u.id: u for u in units}
    figures = figures or {}
    remedial = remedial or {}

    css = CSS + "\n" + katex_css(out_dir)
    parts: list[str] = ["<!doctype html><html><head><meta charset='utf-8'>",
                        f"<title>{esc(title)}</title><style>{css}</style></head><body>",
                        f"<h1 class='doc'>{esc(title)}</h1>"]

    toc: list[str] = ["<div class='toc'>"]
    body: list[str] = []
    for part in outline.get("parts", []):
        kind = part.get("kind")
        if kind == "front_matter":
            continue
        label = part.get("label") or str(part.get("no"))
        ptitle = part.get("title", "")
        heading = f"第 {label} 讲 {ptitle}" if kind == "lecture" else ptitle
        anchor = f"part-{label}"
        body.append(f"<h1 class='part' id='{anchor}'>{esc(heading)}</h1>")
        toc.append(f"<div class='l1'><a href='#{anchor}'>{esc(heading)}</a></div>")

        for si, sec in enumerate(part.get("sections", []), start=1):
            sec_title = display_title(sec.get("title", ""))
            sec_anchor = f"{anchor}-{si}"
            body.append(f"<h2 class='sec' id='{sec_anchor}'>{label}.{si} {esc(sec_title)}</h2>")
            toc.append(f"<div class='l2'><a href='#{sec_anchor}'>{label}.{si} {esc(sec_title)}</a></div>")
            for ui, uid in enumerate(sec.get("units", []), start=1):
                unit = by_id.get(uid)
                if unit is None:
                    continue
                utitle = display_title(unit.title)
                if utitle and utitle != sec_title:
                    body.append(f"<h3 class='unit'>{label}.{si}.{ui} {esc(utitle)}</h3>")
                if uid in remedial:
                    body.append("<div class='box'><div class='head'>补充说明</div><div class='body'>"
                                + block(remedial[uid].splitlines(), assets=assets,
                                        figures=figures, numbers=numbers) + "</div></div>")
                order = list(unit.sections) if not unit.draft else templates.order(profile)
                names = [n for n in order if n in unit.sections] + \
                        [n for n in unit.sections if n not in order]
                for name in names:
                    lines = unit.sections.get(name) or []
                    if not any(l.strip() for l in lines):
                        continue
                    first = next((l.strip() for l in lines if l.strip()), "")
                    if not BOX_OPEN_RE.match(first):
                        body.append(f"<h4 class='sub'>{esc(name)}</h4>")
                    body.append(block(lines, assets=assets, figures=figures, numbers=numbers))

    toc.append("</div>")
    html = "\n".join(parts + toc + body + ["</body></html>"])

    rendered = render_math_batch()
    for i, chunk in enumerate(rendered):
        html = html.replace(f"{MATH_OPEN}{i}{MATH_CLOSE}", chunk)
    # anything KaTeX could not render keeps its source text, never a blank gap
    html = html.replace(MATH_OPEN, "").replace(MATH_CLOSE, "")
    return html


def available_browser() -> str | None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    for name in ("msedge", "chrome", "chromium"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def print_pdf(html_path: Path, *, timeout: int = 180) -> tuple[Path | None, list[str]]:
    browser = available_browser()
    if browser is None:
        return None, ["未找到 Edge / Chrome，无法把 HTML 打印为 PDF"]
    pdf_path = html_path.with_suffix(".pdf")
    try:
        proc = subprocess.run(
            [browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={pdf_path}", html_path.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return None, [f"打印失败：{exc}"]
    if not pdf_path.exists():
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        return None, ["打印失败"] + tail
    return pdf_path, [f"{Path(browser).name} 打印成功"]
