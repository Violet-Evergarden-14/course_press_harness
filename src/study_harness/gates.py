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
    hits = forbidden_hits(rendered_text)
    for line, word, snippet in hits:
        report.add("source_reference_policy", "blocker",
                   f"正文出现来源指称「{word}」：{snippet}",
                   hint="删除指称并直接陈述内容；或设置 render.source_references")


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
    for u in units:
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
    total_slides = {b["loc"]["slide"] for b in blocks
                    if b.get("loc", {}).get("slide") is not None}
    covered = {e.get("loc", {}).get("slide") for u in units for e in u.evidence}
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


def check_numbering(outline: dict[str, Any], report: GateReport) -> None:
    seen: dict[str, str] = {}
    for part in outline.get("parts", []):
        for sec in part.get("sections", []):
            for uid in sec.get("units", []):
                if uid in seen:
                    report.add("numbering_consistency", "blocker",
                               f"知识点重复出现：{uid}", uid)
                seen[uid] = sec.get("title", "")


AGGREGATE_GATES = {"auto_unit_id", "draft_sections", "coverage", "figure_provenance"}


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
    return _compact(r)
