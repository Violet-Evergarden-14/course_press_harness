"""L1 deterministic gates (docs/architecture.md §13, docs/review-protocol.md §1)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .knowledge import REQUIRED_SECTIONS, Unit
from .render import REF_RE, forbidden_hits


@dataclass
class Finding:
    gate: str
    level: str  # blocker | major | minor | info
    message: str
    unit: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"gate": self.gate, "level": self.level, "message": self.message}
        if self.unit:
            d["unit"] = self.unit
        if self.hint:
            d["hint"] = self.hint
        return d


@dataclass
class GateReport:
    findings: list[Finding] = field(default_factory=list)

    def add(self, gate: str, level: str, message: str, unit: str | None = None,
            hint: str | None = None) -> None:
        self.findings.append(Finding(gate, level, message, unit, hint))

    @property
    def failed(self) -> bool:
        return any(f.level == "blocker" for f in self.findings)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.level] = out.get(f.level, 0) + 1
        return out


def check_source_policy(rendered_text: str, mode: str, report: GateReport) -> None:
    if mode == "inline":
        return
    for line, level, matched, snippet in forbidden_hits(rendered_text):
        if level == "blocker":
            report.add("source_reference_policy", "blocker",
                       f"正文指向了素材出处「{matched}」：{snippet}",
                       hint="删除指称并直接陈述内容；或设置 render.source_references")
        else:
            report.add("source_wording", "minor",
                       f"正文出现素材用词「{matched}」：{snippet}",
                       hint="确认是否为内容本身需要；若是转述出处则删除")


def check_refs(units: list[Unit], report: GateReport) -> None:
    known = {u.id for u in units}
    for u in units:
        for name, lines in u.sections.items():
            for line in lines:
                for m in REF_RE.finditer(line):
                    target = m.group(1).strip()
                    if target.startswith(("term:", "asset:", "fig:")):
                        continue  # resolved by their own gates
                    if target not in known:
                        report.add("dangling_refs", "blocker",
                                   f"引用不存在的知识点 [[{target}]]", u.id,
                                   hint="修正引用，或在被引知识点上声明 aliases")


def check_sections(units: list[Unit], report: GateReport) -> None:
    """Required sections apply to structured units, not to composed prose.

    A writer chooses its own section names ("为什么需要", "例子", "易错"); forcing the
    schema's five names onto it would push writing back into a template.
    """
    for u in units:
        if not u.draft:
            filled = [n for n, lines in u.sections.items() if any(l.strip() for l in lines)]
            if len(filled) < 2:
                report.add("compose_thin", "major",
                           f"合成后只有 {len(filled)} 个小节，内容过薄", u.id,
                           hint="补充解释或例子，或与相邻知识点合并")
            continue
        required = REQUIRED_SECTIONS.get(u.type, [])
        missing = [s for s in required if s not in u.sections or not any(
            line.strip() for line in u.sections.get(s, []))]
        if missing:
            level = "major" if u.draft else "blocker"
            report.add("draft_sections" if u.draft else "structure", level,
                       f"缺少小节：{', '.join(missing)}", u.id,
                       hint="补充内容后移除 frontmatter 中的 draft: true")


def check_auto_ids(units: list[Unit], report: GateReport) -> None:
    for u in units:
        if u.id.startswith("auto-"):
            report.add("auto_unit_id", "major",
                       f"知识点 id 为机器生成（{u.id}），语义不可读", u.id,
                       hint="改为 <主题>-<语义 slug>，旧 id 写入 aliases")


URL_RE = re.compile(r"(?:https?|ftp)://\S+")
WIN_PATH_RE = re.compile(r"(?<![\w:/])[A-Za-z]:[\\/]")


def check_path_leak(rendered_text: str, report: GateReport) -> None:
    for line in rendered_text.splitlines():
        # a URL is content, not a leaked build path: drop them before checking
        stripped = URL_RE.sub("", line)
        if WIN_PATH_RE.search(stripped) or ".study/" in stripped or ".study\\" in stripped:
            report.add("path_leak", "blocker", f"产物含路径：{line.strip()[:80]}",
                       hint="资产引用应使用相对产物目录的路径")


def check_coverage(blocks: list[dict[str, Any]], units: list[Unit], report: GateReport) -> None:
    from .knowledge import loc_index as pos

    total_slides = {p for p in (pos(b) for b in blocks) if p is not None}
    covered = {p for p in (pos(e) for u in units for e in u.evidence) if p is not None}
    missing = sorted(s for s in total_slides if s not in covered)
    if missing:
        level = "minor"
        report.add("coverage", level,
                   f"{len(missing)} 张幻灯片未被任何知识点引用：{missing[:12]}",
                   hint="补充知识点，或在 outline 中标记为「略」")


FIGURE_REF_RE = re.compile(r"\[\[fig:([^\]]+)\]\]")


def check_figures(units: list[Unit], figures: dict[str, Any], report: GateReport) -> None:
    """Every referenced figure must exist, be vector-first, and declare its origin."""
    for u in units:
        for _, lines in u.sections.items():
            for line in lines:
                for m in FIGURE_REF_RE.finditer(line):
                    fid = m.group(1).strip()
                    fig = figures.get(fid)
                    if fig is None:
                        report.add("figure_refs", "blocker", f"引用了不存在的图 [[fig:{fid}]]",
                                   u.id, hint="用 `study figure add` 或 `study figure request` 补齐")
                        continue
                    if fig.format in {"png", "jpg", "jpeg"} and fig.provenance != "extracted":
                        report.add("figure_provenance", "major",
                                   f"图 {fid} 为位图但来源是 {fig.provenance}",
                                   u.id,
                                   hint="重画为 mermaid/svg 等文本矢量图，位图只用于照搬素材")
                    if fig.provenance == "synthesized" and not fig.reviewed:
                        report.add("figure_provenance", "minor",
                                   f"补充图 {fid} 尚未复核", u.id,
                                   hint="核对图中内容与正文一致后标记 reviewed: true")


def check_cross_lecture_repeat(units: list[Unit], outline: dict[str, Any],
                               report: GateReport) -> None:
    """Report repeated titles across lectures as info only.

    Deliberately not a merge (docs/architecture.md D1): repetition across lectures is
    teaching rhythm, not redundancy. The author decides whether to link them.
    """
    lecture_of: dict[str, Any] = {}
    for part in outline.get("parts", []):
        if part.get("kind") != "lecture":
            continue
        for sec in part.get("sections", []):
            for uid in sec.get("units", []):
                lecture_of[uid] = part.get("no") or part.get("label")

    by_title: dict[str, list[tuple[str, Any]]] = {}
    for unit in units:
        key = (unit.title or "").strip()
        if not key:
            continue
        by_title.setdefault(key, []).append((unit.id, lecture_of.get(unit.id)))

    for title, entries in sorted(by_title.items()):
        lectures = {lec for _, lec in entries if lec is not None}
        if len(entries) < 2 or len(lectures) < 2:
            continue
        where = "、".join(f"第 {lec} 讲" for _, lec in entries[:4])
        report.add("cross_lecture_repeat", "info",
                   f"「{title}」在多讲出现（{where}）",
                   entries[0][0],
                   hint="保留两处即可；需要互链时在 see_also 中引用")


SECTION_REF_RE = re.compile(r"(?<![\d.])(\d+\.\d+)(?:\.\d+)?(?![\d.])")


def check_section_references(units: list[Unit], outline: dict[str, Any],
                             report: GateReport) -> None:
    """Text that cites 「6.4 节」 must name a section that exists in *this* book.

    Chapters are renumbered when a lecture is added or a section is split, so a number
    copied from the source material silently points at the wrong place — or nowhere.
    """
    valid: set[str] = set()
    chapters: set[str] = set()
    for part in outline.get("parts", []):
        if part.get("kind") == "front_matter":
            continue
        label = str(part.get("label") or part.get("no"))
        chapters.add(label)
        for si, _sec in enumerate(part.get("sections", []), start=1):
            valid.add(f"{label}.{si}")

    for unit in units:
        for name, lines in unit.sections.items():
            for line in lines:
                for m in SECTION_REF_RE.finditer(line):
                    ref = m.group(1)
                    # a decimal such as 2.71 or 9.7 belongs to no chapter label here,
                    # so it is a number, not a section reference
                    if ref.split(".")[0] not in chapters:
                        continue
                    if ref in valid:
                        continue
                    report.add("section_reference", "major",
                               f"引用了不存在的节号「{ref}」", unit.id,
                               hint=f"本书的节号是 {', '.join(sorted(valid)[:6])} …；"
                                    f"改用 [[unit-id]] 引用，或按本书编号书写")


# plain-text maths that should have been written as $...$
UNFORMATTED_MATH = [
    re.compile(r"\^\("),                       # 2^(i-1)
    re.compile(r"[A-Za-z]\s*[≥≤>＜<]\s*\d"),     # n≥0, i>1
    re.compile(r"(?<![A-Za-z.$])[A-Za-z]\s*=\s*[A-Za-z0-9]"),  # n = 0
    re.compile(r"[∑√]"),
    re.compile(r"[⌊⌋]"),
]


def check_unformatted_math(units: list[Unit], report: GateReport) -> None:
    """Formula written as plain text is a typesetting bug, and it is detectable.

    Text like 「n（n≥0）」looks fine in a terminal and wrong in a book: no italic
    variables, no spacing around operators, subscripts collapsed to digits.
    """
    for unit in units:
        for name, lines in unit.sections.items():
            if name == "算式":
                continue
            for line in lines:
                # strip maths that is already formatted
                stripped = re.sub(r"\$[^$]*\$", "", line)
                for pattern in UNFORMATTED_MATH:
                    m = pattern.search(stripped)
                    if m:
                        report.add("unformatted_math", "major",
                                   f"公式未排版：「{m.group(0)}」出现在「{stripped.strip()[:24]}…」",
                                   unit.id,
                                   hint="改用 $...$ 写 LaTeX，例如 $n(n \geq 0)$")
                        break


def check_numbering(outline: dict[str, Any], report: GateReport) -> None:
    seen: dict[str, str] = {}
    for part in outline.get("parts", []):
        for sec in part.get("sections", []):
            for uid in sec.get("units", []):
                if uid in seen:
                    report.add("numbering_consistency", "blocker",
                               f"知识点重复出现：{uid}", uid)
                seen[uid] = sec.get("title", "")


AGGREGATE_GATES = {"auto_unit_id", "draft_sections", "coverage", "figure_provenance", "source_wording", "source_reference_policy"}


def _compact(report: GateReport) -> GateReport:
    """One line per gate for per-unit noise, so the queue stays readable.

    A draft deck can easily produce 90 `auto_unit_id` findings; listing them
    individually buries the real blockers.
    """
    grouped: dict[str, list[Finding]] = {}
    keep: list[Finding] = []
    for f in report.findings:
        if f.gate in AGGREGATE_GATES:
            grouped.setdefault(f.gate, []).append(f)
        else:
            keep.append(f)
    for gate, items in grouped.items():
        level = max((f.level for f in items), key=lambda l: ["info", "minor", "major", "blocker"].index(l))
        example = items[0].message
        msg = f"{len(items)} 处：{example}"
        keep.append(Finding(gate, level, msg, unit=items[0].unit, hint=items[0].hint))
    return GateReport(findings=keep)


def run_gates(
    *,
    units: list[Unit],
    outline: dict[str, Any],
    blocks: list[dict[str, Any]],
    rendered_text: str,
    source_mode: str,
    figures: dict[str, Any] | None = None,
) -> GateReport:
    r = GateReport()
    check_source_policy(rendered_text, source_mode, r)
    check_refs(units, r)
    check_sections(units, r)
    check_auto_ids(units, r)
    check_path_leak(rendered_text, r)
    check_coverage(blocks, units, r)
    check_numbering(outline, r)
    check_figures(units, figures or {}, r)
    check_cross_lecture_repeat(units, outline, r)
    check_section_references(units, outline, r)
    check_unformatted_math(units, r)
    return _compact(r)
