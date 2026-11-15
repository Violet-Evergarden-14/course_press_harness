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

DEFAULT_PREAMBLE = r"""\documentclass[11pt,a4paper,openany]{ctexbook}
\usepackage{geometry}
\geometry{margin=2.4cm}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{array}
\usepackage{longtable}
\usepackage{enumitem}
\setlist{itemsep=2pt,topsep=3pt,leftmargin=1.6em}
\usepackage{xcolor}
\usepackage{fancyhdr}
\usepackage{listings}
\usepackage{tikz}
\lstset{basicstyle=\ttfamily\small,breaklines=true,columns=fullflexible}
\usepackage[hidelinks]{hyperref}
\setlength{\parskip}{0.5em}
\setlength{\parindent}{0pt}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small\leftmark}
\fancyhead[R]{\small\thepage}
\renewcommand{\headrulewidth}{0.4pt}
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


def md_inline_to_tex(text: str) -> str:
    """Turn the markdown emphasis drafts actually use into LaTeX; leave math alone."""
    def bold(m: re.Match) -> str:
        return r"\textbf{" + m.group(1) + "}"

    def italic(m: re.Match) -> str:
        return r"\emph{" + m.group(1) + "}"

    parts = INLINE_MATH_RE.split(text or "")
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            out.append(part)
        else:
            out.append(ITALIC_RE.sub(italic, BOLD_RE.sub(bold, part)))
    return "".join(out)


def escape_tex(text: str) -> str:
    """Escape prose while leaving `$...$` math untouched."""
    parts = INLINE_MATH_RE.split(text or "")
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

    lines: list[str] = [p["preamble"].rstrip(), "", r"\begin{document}", ""]
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


def _unit_body(unit: Unit, profile: str, notes: list[dict[str, Any]],
               templates: TemplateSet, assets_dir: Path | None,
               figures: dict[str, Any]) -> list[str]:
    out: list[str] = []
    order = templates.order(profile)
    names = [n for n in order if n in unit.sections] + [n for n in unit.sections if n not in order]
    pending_table: list[str] = []
    for name in names:
        body = unit.sections.get(name) or []
        if not any(line.strip() for line in body):
            continue
        out.append(r"\paragraph{" + escape_tex(name) + "}")
        for raw in body:
            line = raw.strip()
            if not line:
                continue
            m = EQUATION_RE.match(line)
            if m:
                out += [r"\begin{equation*}", m.group(1).strip(), r"\end{equation*}"]
                continue
            if line.startswith("|"):
                pending_table.append(line)   # markdown table: emit as tabular below
                continue
            if pending_table:
                out += _md_table(pending_table)
                pending_table = []
            out.append(_inline_line(line, assets_dir, figures))
        if name == "表格":
            out += _tables(unit)
        if pending_table:
            out += _md_table(pending_table)
            pending_table = []
        out.append("")
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
                return "\n" + fig.path.read_text(encoding="utf-8")
            body = fig.path.read_text(encoding="utf-8").strip()
            return "\n" + r"\begin{lstlisting}[basicstyle=\ttfamily\footnotesize]" + "\n" + body + \
                   "\n" + r"\end{lstlisting}"
        if target.startswith("term:"):
            return escape_tex(alias or target[5:])
        return escape_tex(alias or target)

    # escape first, then convert emphasis: otherwise the 	extbf we generate
    # would itself get escaped and print literally
    return md_inline_to_tex(escape_tex_outside_refs(line, repl))


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
    out = [r"\begin{center}\small\begin{tabular}{" + ("l" * cols) + "}",
           r"\toprule"]
    out.append(" & ".join(escape_tex(c) for c in rows[0]) + r" \\")
    out.append(r"\midrule")
    for row in rows[1:]:
        out.append(" & ".join(escape_tex(c) for c in row) + r" \\")
    out += [r"\bottomrule", r"\end{tabular}\end{center}"]
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
