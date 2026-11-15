"""LaTeX / PDF rendering (docs/rendering.md §3, §12).

The default class is `ctexbook`: a printed book, not a web page — page numbers,
running heads, a real table of contents, and no invented timestamps. The preamble is
data (`templates/<profile>/tex/manifest.yaml`), so a reader can replace it wholesale
with their own style without touching code.

Compilation is best effort and never faked: if no engine is available, the `.tex` is
still written and the caller is told why no PDF exists.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .knowledge import Unit
from .render import display_title, number_units
from .templates import TemplateSet

DEFAULT_PREAMBLE = r"""\documentclass[12pt,a4paper,openany]{ctexbook}
\usepackage{geometry}
\geometry{a4paper,margin=2.5cm}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{array}
\usepackage{longtable}
\usepackage{booktabs}
\usepackage[table]{xcolor}
\usepackage{colortbl}
\usepackage{enumitem}
\usepackage{xcolor}
\usepackage[most]{tcolorbox}
\usepackage{fancyhdr}
\usepackage{listings}
\usepackage{tikz}
\usepackage{setspace}
\usepackage{adjustbox}
\usepackage{tabularx}
\usepackage[hidelinks]{hyperref}

% ---- palette measured from the reference study document -------------------
\definecolor{headnavy}{HTML}{1F3864}
\definecolor{tablehead}{HTML}{DDE5F0}
\definecolor{notebg}{HTML}{EAF1FB}
\definecolor{noterule}{HTML}{9DC3E6}
\definecolor{notebar}{HTML}{2F5597}
\definecolor{warnbg}{HTML}{FDF0E6}
\definecolor{warnrule}{HTML}{F4B183}
\definecolor{warnbar}{HTML}{C55A11}
\definecolor{headgray}{HTML}{7F7F7F}

% ---- fonts: serif body, sans headings (宋体 + 黑体) ------------------------
\ctexset{
  chapter/name={第,讲},
  chapter/format={\sffamily\bfseries\LARGE\color{headnavy}},
  chapter/nameformat={\sffamily\bfseries\LARGE\color{headnavy}},
  chapter/titleformat={\sffamily\bfseries\LARGE\color{headnavy}},
  section/format={\sffamily\bfseries\large\color{headnavy}},
  subsection/format={\sffamily\bfseries\normalsize\color{headnavy}},
  subsubsection/format={\sffamily\bfseries\normalsize\color{headnavy}},
}

% ---- spacing: generous leading, 2em first-line indent ---------------------
\AtBeginDocument{\linespread{1.30}\selectfont\setlength{\parskip}{0pt}}
\setlength{\parindent}{2em}
\setlength{\parskip}{0pt}
% ctexbook's \paragraph carries a large beforeskip that cannot be tuned from here;
% a heading inside a knowledge point needs 5pt before and 2pt after, nothing more.
\newcommand{\unitsec}[1]{\par\vspace{9pt}\noindent{\sffamily\bfseries\color{headnavy}#1}\par\vspace{3pt}}
\setlist[itemize]{itemsep=3pt,topsep=2pt,leftmargin=1.9em,labelsep=0.6em}
\setlist[enumerate]{itemsep=3pt,topsep=2pt,leftmargin=2.1em}

% ---- two kinds of call-out boxes (blue = definition, orange = pitfall) ----
\newtcolorbox{nbox}[1]{
  enhanced, colback=notebg, colframe=noterule, boxrule=0.4pt, arc=1pt,
  left=10pt,right=10pt,top=6pt,bottom=6pt,
  colbacktitle=notebar, coltitle=white, fonttitle=\sffamily\bfseries\small,
  title={#1}, toptitle=2pt, bottomtitle=2pt, before skip=10pt, after skip=10pt,
  breakable,
}
\newtcolorbox{wbox}[1]{
  enhanced, colback=warnbg, colframe=warnrule, boxrule=0.4pt, arc=1pt,
  left=10pt,right=10pt,top=6pt,bottom=6pt,
  colbacktitle=warnbar, coltitle=white, fonttitle=\sffamily\bfseries\small,
  title={#1}, toptitle=2pt, bottomtitle=2pt, before skip=10pt, after skip=10pt,
  breakable,
}

% ---- tables: shaded header, no vertical rules -----------------------------
\renewcommand{\arraystretch}{1.25}
\newcommand{\thead}[1]{\rowcolor{tablehead}\sffamily\bfseries\color{headnavy}#1}

% ---- running heads -------------------------------------------------------
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small\color{headgray}\booktitle}
\fancyhead[R]{\small\color{headgray}\leftmark}
\fancyfoot[C]{\small\color{headgray}\thepage}
\renewcommand{\headrulewidth}{0.3pt}
\fancypagestyle{plain}{\fancyhf{}\fancyfoot[C]{\small\color{headgray}\thepage}%
  \renewcommand{\headrulewidth}{0pt}}

\lstset{basicstyle=\ttfamily\small,breaklines=true,columns=fullflexible,
  frame=none, aboveskip=0.5em, belowskip=0.5em}
"""

# Unicode math the CJK font does not carry: map to LaTeX commands, otherwise the glyph
# comes out as a replacement box in the PDF.
UNICODE_MATH = {
    "≥": r"$\geq$", "≤": r"$\leq$", "≠": r"$\neq$", "≈": r"$\approx$",
    "→": r"$\rightarrow$", "←": r"$\leftarrow$", "×": r"$\times$",
    "÷": r"$\div$", "−": "-", "∞": r"$\infty$", "∈": r"$\in$",
    "∪": r"$\cup$", "∩": r"$\cap$", "∑": r"$\sum$", "√": r"$\sqrt{}$",
    "±": r"$\pm$", "·": r"$\cdot$", "∅": r"$\varnothing$",
    "⌊": r"$\lfloor$", "⌋": r"$\rfloor$", "⌈": r"$\lceil$", "⌉": r"$\rceil$",
    "✓": r"$\checkmark$", "✗": r"$\times$", "⇔": r"$\Leftrightarrow$",
    "⇒": r"$\Rightarrow$", "↔": r"$\leftrightarrow$", "≡": r"$\equiv$",
    "ᵢ": r"$_{i}$", "ⱼ": r"$_{j}$", "₀": r"$_{0}$", "₁": r"$_{1}$", "₂": r"$_{2}$",
    "ₙ": r"$_{n}$", "ᴺ": r"$^{N}$", "²": r"$^{2}$", "³": r"$^{3}$",
}

TEX_SPECIALS = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}

EQUATION_RE = re.compile(r"^\$\$(.*)\$\$$", re.S)
INLINE_MATH_RE = re.compile(r"(\$[^$]+\$)")
REF_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


BOLD_OPEN, BOLD_CLOSE = "\x01", "\x02"
ITAL_OPEN, ITAL_CLOSE = "\x03", "\x04"
CODE_OPEN, CODE_CLOSE = "\x05", "\x06"


def md_inline_to_tex(text: str) -> str:
    """Turn the markdown emphasis drafts use into LaTeX, leaving `$...$` alone."""
    def bold(m: re.Match) -> str:
        return BOLD_OPEN + m.group(1) + BOLD_CLOSE

    def italic(m: re.Match) -> str:
        return ITAL_OPEN + m.group(1) + ITAL_CLOSE

    parts = INLINE_MATH_RE.split(text or "")
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
        else:
            out.append(ITALIC_RE.sub(italic, BOLD_RE.sub(bold, part)))
    return "".join(out)


INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def inline_code_to_markers(text: str) -> str:
    return INLINE_CODE_RE.sub(lambda m: CODE_OPEN + m.group(1) + CODE_CLOSE, text)


def emph_to_markers(text: str) -> str:
    """Emphasis first, escaping second.

    Escaping runs first in the naive order, and it turns "≥" into "$\\geq$"; the
    inserted math span then splits "**...**" so the bold regex never matches and the
    asterisks leak into the PDF.
    """
    return md_inline_to_tex(inline_code_to_markers(text))


def markers_to_tex(text: str) -> str:
    return (text.replace(BOLD_OPEN, r"\textbf{").replace(BOLD_CLOSE, "}")
                .replace(ITAL_OPEN, r"\emph{").replace(ITAL_CLOSE, "}")
                .replace(CODE_OPEN, r"\texttt{").replace(CODE_CLOSE, "}"))


SUP_PAREN_RE = re.compile(r"([A-Za-z0-9])\^\(([^)]{1,12})\)")
SUP_WORD_RE = re.compile(r"([A-Za-z0-9])\^([A-Za-z0-9])")
SUB_RE = re.compile(r"(?<![A-Za-z0-9_$\\])([A-Za-z])([0-9])(?![0-9A-Za-z])")


def subscripts(text: str) -> str:
    """Turn `n0`, `n2`, `T1` into real subscripts.

    Identifiers written as plain text (`n0 = n2 + 1`) look like typos in a printed
    book; they are subscripts and must be set as such.
    """
    return SUB_RE.sub(lambda m: f"${m.group(1)}_{{{m.group(2)}}}$", text)


def superscripts(text: str) -> str:
    """Turn `2^(i-1)` / `2^k` into real superscripts.

    The composer writes formulas in plain text; left alone they print as literal
    carets, which is unreadable in a maths-heavy chapter.
    """
    text = SUP_PAREN_RE.sub(lambda m: f"${m.group(1)}^{{{m.group(2)}}}$", text)
    return SUP_WORD_RE.sub(lambda m: f"${m.group(1)}^{{{m.group(2)}}}$", text)


def escape_tex(text: str) -> str:
    """Escape prose while leaving `$...$` math untouched."""
    parts = INLINE_MATH_RE.split(superscripts(subscripts(text or "")))
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # math span, already LaTeX
            out.append(part)
        else:
            out.append("".join(
                UNICODE_MATH.get(ch) or TEX_SPECIALS.get(ch, ch) for ch in part))
    return "".join(out)


def _paths(templates: TemplateSet) -> dict[str, str]:
    tex = templates.data.get("latex", {}) if isinstance(templates.data.get("latex"), dict) else {}
    return {
        "preamble": tex.get("preamble") or DEFAULT_PREAMBLE,
        "chapter": tex.get("chapter") or r"\chapter{%s}",
        "section": tex.get("section") or r"\section{%s}",
        "subsection": tex.get("subsection") or r"\subsection{%s}",
        "titlepage": tex.get("titlepage") or "",
    }


def build_tex(
    units: list[Unit],
    outline: dict[str, Any],
    *,
    title: str,
    profile: str = "learn",
    templates: TemplateSet | None = None,
    assets_dir: Path | None = None,
    notes: list[dict[str, Any]] | None = None,
    remedial: dict[str, str] | None = None,
    figures: dict[str, Any] | None = None,
) -> str:
    templates = templates or TemplateSet()
    p = _paths(templates)
    numbers = number_units(outline)
    by_id = {u.id: u for u in units}
    notes = notes or []
    remedial = remedial or {}
    figures = figures or {}

    lines: list[str] = [p["preamble"].rstrip(), "",
                        r"\newcommand{\booktitle}{" + escape_tex(title) + "}",
                        "", r"\begin{document}", ""]
    lines += [r"\frontmatter", r"\tableofcontents", r"\mainmatter", ""]

    titlepage = p["titlepage"]
    if titlepage:
        lines += [titlepage.replace("{title}", escape_tex(title)), r"\clearpage", ""]
    else:
        lines += [r"\begin{titlepage}", r"\centering",
                  r"{\Huge\bfseries " + escape_tex(title) + r"}\vspace{2em}", r"\end{titlepage}", ""]

    for part in outline.get("parts", []):
        kind = part.get("kind")
        if kind == "front_matter":
            continue
        label = part.get("label") or str(part.get("no"))
        heading = f"第 {label} 讲 {part.get('title', '')}" if kind == "lecture" else part.get("title", "")
        if kind == "lecture":
            # the lecture number *is* the chapter number: keep them identical instead of
            # printing 「第一章 第 6 讲」
            try:
                lines.append(r"\setcounter{chapter}{%d}" % (int(label) - 1))
            except (TypeError, ValueError):
                pass
            lines.append(p["chapter"] % escape_tex(str(part.get("title", ""))))
        else:
            lines.append(p["chapter"] % escape_tex(heading))
        lines.append("")

        for sec in part.get("sections", []):
            sec_title = display_title(sec.get("title", ""))
            if len(sec.get("units", [])) > 1 or sec_title:
                lines.append(p["section"] % escape_tex(sec_title))
                lines.append("")
            for uid in sec.get("units", []):
                unit = by_id.get(uid)
                if unit is None:
                    continue
                utitle = display_title(unit.title)
                if utitle and utitle != sec_title:
                    lines.append(p["subsection"] % escape_tex(utitle))
                    lines.append("")
                if uid in remedial:
                    lines.append(r"\begin{quote}\textbf{补充说明}")
                    lines.append(escape_tex(remedial[uid]))
                    lines.append(r"\end{quote}")
                    lines.append("")
                lines += _unit_body(unit, profile, notes, templates, assets_dir, figures)
                lines.append("")

    lines += _appendices(units, outline, numbers, profile, templates)
    lines += [r"\end{document}", ""]
    return "\n".join(lines)


BOX_OPEN_RE = re.compile(r"^:::(note|warn)\s*(.*)$")
BULLET_RE = re.compile(r"^[-*•]\s+(.*)$")
NUM_ITEM_RE = re.compile(r"^\d+[.、)]\s+(.*)$")


def _unit_body(unit: Unit, profile: str, notes: list[dict[str, Any]],
               templates: TemplateSet, assets_dir: Path | None,
               figures: dict[str, Any]) -> list[str]:
    out: list[str] = []
    order = templates.order(profile)
    names = [n for n in order if n in unit.sections] + [n for n in unit.sections if n not in order]
    for name in names:
        body = unit.sections.get(name) or []
        if not any(line.strip() for line in body):
            continue
        # a section that *is* a call-out box already has a title bar; printing the
        # paragraph heading above it would title the same thing twice
        first = next((l.strip() for l in body if l.strip()), "")
        if not BOX_OPEN_RE.match(first):
            out.append(r"\unitsec{" + escape_tex(name) + "}")
        out += _render_blocks(body, assets_dir, figures)
        if name == "表格":
            out += _tables(unit)
        out.append("")
    return out


def _render_blocks(body: list[str], assets_dir: Path | None,
                   figures: dict[str, Any]) -> list[str]:
    """Turn composer markdown into LaTeX: ::: boxes, bullet lists, pipe tables, math."""
    out: list[str] = []
    table: list[str] = []
    bullets: list[str] = []
    numbers: list[str] = []
    box: tuple[str, str, list[str]] | None = None

    def flush_table() -> None:
        nonlocal table
        if table:
            out.extend(_md_table(table))
            table = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            out.append(r"\begin{itemize}")
            for item in bullets:
                out.append(r"  \item " + _inline_line(item, assets_dir, figures))
            out.append(r"\end{itemize}")
            bullets = []

    def flush_numbers() -> None:
        nonlocal numbers
        if numbers:
            out.append(r"\begin{enumerate}")
            for item in numbers:
                out.append(r"  \item " + _inline_line(item, assets_dir, figures))
            out.append(r"\end{enumerate}")
            numbers = []

    def emit_line(line: str) -> None:
        m = EQUATION_RE.match(line)
        if m:
            flush_numbers()
            out.extend([r"\begin{equation*}", m.group(1).strip(), r"\end{equation*}"])
            return
        if line.startswith("|"):
            flush_bullets()
            flush_numbers()
            table.append(line)
            return
        flush_table()
        bm = BULLET_RE.match(line)
        if bm:
            flush_numbers()
            bullets.append(bm.group(1).strip())
            return
        nm = NUM_ITEM_RE.match(line)
        if nm:
            flush_bullets()
            numbers.append(nm.group(1).strip())
            return
        flush_bullets()
        flush_numbers()
        # one source line = one paragraph: without an explicit break, LaTeX joins
        # consecutive lines and every 步骤/证明 step collapses into one blob
        out.append(_inline_line(line, assets_dir, figures))
        out.append("")

    code: list[str] | None = None
    for raw in body:
        line = raw.strip()
        # a fenced block belongs to whatever it sits in: while a box is open, the fence
        # must be collected as box content, not consume the box's closing marker
        if box is None:
            if code is not None:
                if line.startswith("```"):
                    flush_bullets(); flush_numbers(); flush_table()
                    out.extend([r"\begin{lstlisting}"] + code + [r"\end{lstlisting}"])
                    code = None
                else:
                    code.append(raw)  # keep indentation: this is code, not prose
                continue
            if line.startswith("```"):
                flush_bullets(); flush_numbers(); flush_table()
                code = []
                continue
        if not line:
            continue
        if box is not None:
            if line == ":::":
                kind, title, inner = box
                flush_bullets()
                flush_numbers()
                flush_table()
                env = "nbox" if kind == "note" else "wbox"
                out.append(r"\begin{%s}{%s}" % (env, escape_tex(title)))
                out.extend(_render_blocks(inner, assets_dir, figures))
                out.append(r"\end{%s}" % env)
                box = None
            else:
                box[2].append(line)
            continue
        opener = BOX_OPEN_RE.match(line)
        if opener:
            flush_bullets()
            flush_numbers()
            flush_table()
            box = (opener.group(1), opener.group(2).strip(), [])
            continue
        emit_line(line)

    if box is not None:            # unclosed box: emit as a note rather than dropping it
        kind, title, inner = box
        env = "nbox" if kind == "note" else "wbox"
        flush_bullets()
        flush_numbers()
        flush_table()
        out.append(r"\begin{%s}{%s}" % (env, escape_tex(title)))
        out.extend(_render_blocks(inner, assets_dir, figures))
        out.append(r"\end{%s}" % env)

    flush_bullets()
    flush_numbers()
    flush_table()
    return out


def _inline_line(line: str, assets_dir: Path | None, figures: dict[str, Any]) -> str:
    def repl(m: re.Match) -> str:
        target, alias = m.group(1).strip(), m.group(2)
        if target.startswith("asset:"):
            name = target[6:]
            for ext in (".png", ".jpg", ".jpeg", ".pdf", ".svg"):
                if assets_dir is not None and (assets_dir / f"{name}{ext}").exists():
                    return (r"\includegraphics[width=0.8\linewidth]{" +
                            (assets_dir / f"{name}{ext}").as_posix() + "}")
            return ""
        if target.startswith("fig:"):
            fig = figures.get(target[4:])
            if fig is None:
                return ""
            if fig.format in {"png", "jpg", "jpeg", "svg"}:
                return r"\includegraphics[width=0.8\linewidth]{" + fig.path.as_posix() + "}"
            if fig.format == "tikz":
                # never let a drawing exceed the text block: shrink, never crop
                body = fig.path.read_text(encoding="utf-8")
                return ("\n" + r"\begin{center}\adjustbox{max width=\linewidth}{"
                        + body + r"}\end{center}" + "\n")
            body = fig.path.read_text(encoding="utf-8").strip()
            return "\n" + r"\begin{lstlisting}[basicstyle=\ttfamily\footnotesize]" + "\n" + body + \
                   "\n" + r"\end{lstlisting}"
        if target.startswith("term:"):
            return escape_tex(alias or target[5:])
        return escape_tex(alias or target)

    # escape first, then convert emphasis: otherwise the 	extbf we generate
    # would itself get escaped and print literally
    return markers_to_tex(escape_tex_outside_refs(emph_to_markers(line), repl))


def escape_tex_outside_refs(line: str, repl) -> str:
    """Escape text but let reference callbacks inject raw LaTeX."""
    pieces: list[str] = []
    last = 0
    for m in REF_RE.finditer(line):
        pieces.append(escape_tex(line[last:m.start()]))
        pieces.append(repl(m))
        last = m.end()
    pieces.append(escape_tex(line[last:]))
    return "".join(pieces)


def _md_table(lines: list[str]) -> list[str]:
    """Render a markdown pipe table (written by the composer) as a LaTeX tabular."""
    rows: list[list[str]] = []
    for raw in lines:
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        if cells and not set("".join(cells)) <= set("-: "):
            rows.append(cells)
    if len(rows) < 2:
        return []
    cols = max(len(r) for r in rows)
    # `l` columns never wrap: long cells overflow and the table reads as scrambled.
    # First column keeps its natural width, the rest share what is left.
    if cols == 3:
        spec = r"l >{\hsize=0.72\hsize}X >{\hsize=1.28\hsize}X"
    elif cols > 1:
        spec = "l" + "X" * (cols - 1)
    else:
        spec = "l"
    out = [r"\begin{center}\small\begin{tabularx}{\linewidth}{" + spec + "}",
           r"\toprule"]
    # shaded sans-serif header, no vertical rules — as in the reference document
    out.append(r"\thead{" + " & ".join(md_inline_to_tex(escape_tex(c)) for c in rows[0]) + r"} \\")
    out.append(r"\midrule")
    for row in rows[1:]:
        out.append(" & ".join(markers_to_tex(escape_tex(emph_to_markers(c))) for c in row) + r" \\")
    out += [r"\bottomrule", r"\end{tabularx}\end{center}"]
    return out


def _tables(unit: Unit) -> list[str]:
    out: list[str] = []
    for raw in unit.sections.get("表格") or []:
        rows = [r.strip().strip("|").split("|") for r in raw.splitlines() if r.strip().startswith("|")]
        rows = [[c.strip() for c in row] for row in rows if not set("".join(row)) <= set("- ")]
        if len(rows) < 2:
            continue
        cols = max(len(r) for r in rows)
        spec = "l" * cols
        out.append(r"\begin{center}\begin{tabular}{" + spec + "}")
        out.append(r"\toprule")
        out.append(" & ".join(escape_tex(c) for c in rows[0]) + r" \\")
        out.append(r"\midrule")
        for row in rows[1:]:
            out.append(" & ".join(escape_tex(c) for c in row) + r" \\")
        out.append(r"\bottomrule")
        out.append(r"\end{tabular}\end{center}")
    return out


def _appendices(units: list[Unit], outline: dict[str, Any], numbers: dict[str, str],
                profile: str, templates: TemplateSet) -> list[str]:
    out: list[str] = []
    order = [uid for part in outline.get("parts", []) for sec in part.get("sections", [])
             for uid in sec.get("units", [])]
    by_id = {u.id: u for u in units}

    if profile == "review":
        focus = [u for u in (by_id.get(uid) for uid in order) if u is not None and u.emphasis]
        if focus:
            out += [r"\appendix", r"\chapter{重点清单}", r"\begin{itemize}"]
            for u in focus:
                out.append(r"\item " + escape_tex(u.title))
            out += [r"\end{itemize}", ""]

    formulas = [(uid, by_id[uid]) for uid in order if by_id.get(uid) and by_id[uid].sections.get("算式")]
    if formulas:
        out += [r"\appendix", r"\chapter{公式速查}", ""]
        for uid, unit in formulas:
            out.append(r"\paragraph{" + escape_tex(display_title(unit.title)) + "}")
            for raw in unit.sections.get("算式") or []:
                m = EQUATION_RE.match(raw.strip())
                out += [r"\begin{equation*}", m.group(1).strip() if m else escape_tex(raw),
                        r"\end{equation*}"]
        out.append("")
    return out


def available_engine() -> str | None:
    for exe in ("xelatex", "lualatex", "pdflatex"):
        if shutil.which(exe):
            return exe
    return None


def compile_pdf(tex_path: Path, *, engine: str | None = None, timeout: int = 300) -> tuple[Path | None, list[str]]:
    """Compile twice (TOC + refs). Returns (pdf_path or None, log lines)."""
    exe = engine or available_engine()
    if exe is None:
        return None, ["未找到 LaTeX 引擎（xelatex / lualatex / pdflatex）"]

    logs: list[str] = []
    out_dir = tex_path.parent
    for run in (1, 2):
        try:
            proc = subprocess.run(
                [shutil.which(exe) or exe, "-interaction=nonstopmode", "-halt-on-error",
                 tex_path.name],
                cwd=str(out_dir), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return None, [f"{exe} 执行失败：{exc}"]
        if proc.returncode != 0:
            tail = (proc.stdout or "").strip().splitlines()[-25:]
            logs.extend([f"{exe} 第 {run} 遍失败"] + tail)
            pdf = tex_path.with_suffix(".pdf")
            if pdf.exists():
                pdf.unlink()
            return None, logs
        logs.append(f"{exe} 第 {run} 遍成功")

    for ext in (".aux", ".out", ".toc", ".log"):
        aux = tex_path.with_suffix(ext)
        if aux.exists():
            aux.unlink()  # logs are returned in memory; keep the output dir clean
    pdf = tex_path.with_suffix(".pdf")
    return (pdf if pdf.exists() else None), logs
