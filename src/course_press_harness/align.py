"""Audio <-> slide alignment (docs/alignment.md).

Three tiers, decided from probe data, never from a model:

    A  anchors exist  -> interpolate between anchors
    B  no anchors     -> lexical dynamic programming with a monotone constraint
    C  no audio       -> skip entirely

The monotone constraint is the whole trick: lecture slides are presented in order, so
the slide index along the transcript must be non-decreasing. That single assumption
removes most of the ambiguity that pure similarity matching leaves behind.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .ir import sha256_text

NGRAM_N = (2, 3)
TIMECODE_RE = re.compile(r"(?<!\d)(\d{1,2}):([0-5]\d)(?::([0-5]\d))?(?!\d)")

# DP penalties (docs/alignment.md §5.3)
SKIP_PENALTY = 0.02
GAP_PENALTY = 0.03
UNASSIGNED_PENALTY = 0.15
LOW_CONFIDENCE = 0.6


@dataclass
class Segment:
    block: str
    start: float
    end: float
    slide: int | None = None
    confidence: float = 0.0
    method: str = "lexical_dp"
    unconfirmed: bool = False
    text: str = ""

    def to_json(self) -> dict[str, Any]:
        d = {"block": self.block, "t": [self.start, self.end]}
        if self.slide is not None:
            d["slides"] = [self.slide]
        d["confidence"] = round(self.confidence, 3)
        d["method"] = self.method
        if self.unconfirmed:
            d["unconfirmed"] = True
        return d


@dataclass
class Alignment:
    lecture: int | None
    tier: str
    segments: list[Segment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        vals = sorted(s.confidence for s in self.segments if s.slide is not None)
        return round(vals[len(vals) // 2], 3) if vals else 0.0

    def to_json(self) -> dict[str, Any]:
        slide_to_segments: dict[str, list[str]] = {}
        for s in self.segments:
            if s.slide is not None:
                slide_to_segments.setdefault(str(s.slide), []).append(s.block)
        return {
            "schema_version": 1,
            "lecture": self.lecture,
            "tier": self.tier,
            "confidence": self.confidence,
            "segments": [s.to_json() for s in self.segments],
            "slide_to_segments": slide_to_segments,
            "unassigned": [s.block for s in self.segments if s.slide is None],
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# tier decision
# --------------------------------------------------------------------------

def decide_tier(probe: dict[str, Any], audio_present: bool, anchor_count: int) -> str:
    if not audio_present:
        return "C"
    if anchor_count >= 3:
        return "A"
    if anchor_count >= 1:
        return "A"
    return "B"


def collect_anchors(notes: Iterable[dict[str, Any]], slide_texts: dict[int, str]) -> list[tuple[float, int]]:
    """Anchors from student notes: `{slide: n, t: [start, end]}`."""
    anchors: list[tuple[float, int]] = []
    for note in notes:
        loc = note.get("loc") or {}
        slide = loc.get("slide")
        t = loc.get("t")
        if isinstance(slide, int) and isinstance(t, (list, tuple)) and t:
            anchors.append((float(t[0]), int(slide)))
    anchors.sort()
    return anchors


def anchors_from_notes_text(notes_text: str) -> list[tuple[float, int | None]]:
    """"第 12 页 @13:40" style cues found in speaker notes."""
    out: list[tuple[float, int | None]] = []
    for m in re.finditer(r"第\s*(\d+)\s*页[^\d]{0,6}(\d{1,2}):([0-5]\d)(?::([0-5]\d))?", notes_text):
        page = int(m.group(1))
        h_or_m, m_or_s, maybe = m.group(2), m.group(3), m.group(4)
        seconds = int(h_or_m) * 3600 + int(m_or_s) * 60 + int(maybe) if maybe else \
            int(h_or_m) * 60 + int(m_or_s)
        out.append((float(seconds), page))
    return out


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------

def _ngrams(text: str) -> dict[str, int]:
    clean = re.sub(r"[\s\W_]+", "", text or "")
    grams: dict[str, int] = {}
    for n in NGRAM_N:
        for i in range(len(clean) - n + 1):
            g = clean[i:i + n]
            grams[g] = grams.get(g, 0) + 1
    return grams


def similarity(a: dict[str, int], b: dict[str, int]) -> float:
    if not a or not b:
        return 0.0
    overlap = sum(min(v, b.get(k, 0)) for k, v in a.items())
    norm = (sum(a.values()) ** 0.5) * (sum(b.values()) ** 0.5)
    return overlap / norm if norm else 0.0


def slide_features(slide_texts: dict[int, str]) -> dict[int, dict[str, int]]:
    return {n: _ngrams(t) for n, t in slide_texts.items()}


# --------------------------------------------------------------------------
# tier A: interpolation
# --------------------------------------------------------------------------

def align_by_anchors(segments: list[Segment], anchors: list[tuple[float, int]],
                     slide_ids: list[int]) -> Alignment:
    al = Alignment(lecture=None, tier="A")
    if not anchors:
        return al

    def slide_at(t: float) -> tuple[int, float]:
        if t <= anchors[0][0]:
            return anchors[0][1], 0.9
        if t >= anchors[-1][0]:
            return anchors[-1][1], 0.9
        for (t0, s0), (t1, s1) in zip(anchors, anchors[1:]):
            if t0 <= t <= t1:
                if s1 == s0:
                    return s0, 0.95
                span = max(1e-6, t1 - t0)
                frac = (t - t0) / span
                slide = s0 + int((s1 - s0) * frac)
                slide = max(0, min(slide_ids[-1] if slide_ids else slide, slide))
                return slide, 0.9
        return anchors[-1][1], 0.8

    for seg in segments:
        mid = (seg.start + seg.end) / 2
        seg.slide, seg.confidence = slide_at(mid)
        seg.method = "anchor"
    al.segments = segments

    covered = {s.slide for s in segments}
    for sid in slide_ids:
        if sid not in covered:
            al.notes.append(f"第 {sid} 张没有对应讲解")
    return al


# --------------------------------------------------------------------------
# tier B: monotone dynamic programming
# --------------------------------------------------------------------------

def align_by_dp(segments: list[Segment], slide_texts: dict[int, str],
                anchors: list[tuple[float, int]] | None = None) -> Alignment:
    slide_ids = sorted(slide_texts)
    feats = slide_features(slide_texts)
    n, m = len(segments), len(slide_ids)
    al = Alignment(lecture=None, tier="B")
    if not n or not m:
        al.segments = segments
        return al

    seg_feats = [_ngrams(s.text) for s in segments]
    anchor_slide = {s for _, s in (anchors or [])}

    NEG = float("-inf")
    # score[i][j]: best score for first i segments with segment i assigned to slide j
    score = [[NEG] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    score[0][0] = 0.0

    for i in range(1, n + 1):
        # option: leave unassigned (slide index 0)
        best, best_k = NEG, 0
        for k in range(m + 1):
            if score[i - 1][k] > best:
                best, best_k = score[i - 1][k], k
        score[i][0] = best - UNASSIGNED_PENALTY
        back[i][0] = best_k

        prefix_best = [NEG] * (m + 2)
        prefix_arg = [0] * (m + 2)
        run, arg = NEG, 0
        for k in range(m + 1):
            if score[i - 1][k] > run:
                run, arg = score[i - 1][k], k
            prefix_best[k] = run
            prefix_arg[k] = arg

        for j in range(1, m + 1):
            sim = similarity(seg_feats[i - 1], feats[slide_ids[j - 1]])
            if slide_ids[j - 1] in anchor_slide:
                sim += 0.05

            candidates = [
                (score[i - 1][j] + sim - SKIP_PENALTY, j),                  # same slide again
                (score[i - 1][j - 1] + sim, j - 1),                          # advance one
                (prefix_best[j - 1] + sim - GAP_PENALTY * 1, prefix_arg[j - 1]),  # skip ahead
            ]
            if j >= 2:
                candidates.append(
                    (prefix_best[j - 2] + sim - GAP_PENALTY * 2, prefix_arg[j - 2]))
            val, k = max(candidates, key=lambda x: x[0])
            score[i][j] = val
            back[i][j] = k

    # backtrack from the best end state
    best_val, best_j = max((score[n][j], j) for j in range(m + 1))
    assigned: list[int | None] = [None] * n
    j = best_j
    for i in range(n, 0, -1):
        assigned[i - 1] = slide_ids[j - 1] if j > 0 else None
        j = back[i][j]

    for i, seg in enumerate(segments):
        seg.slide = assigned[i]
        if seg.slide is None:
            seg.confidence = 0.0
        else:
            sim = similarity(seg_feats[i], feats[seg.slide])
            # margin against the runner-up slide
            others = sorted((similarity(seg_feats[i], feats[s]) for s in slide_ids
                             if s != seg.slide), reverse=True)
            margin = sim - (others[0] if others else 0.0)
            seg.confidence = max(0.0, min(1.0, 0.5 + 2.0 * margin + 1.5 * sim))
            seg.method = "lexical_dp"
            seg.unconfirmed = seg.confidence < LOW_CONFIDENCE
            if seg.unconfirmed:
                al.notes.append(f"{seg.block} 置信度低（{seg.confidence:.2f}），需人工确认")

    al.segments = segments
    return al


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def slide_texts_from_blocks(blocks: list[dict[str, Any]]) -> dict[int, str]:
    out: dict[int, str] = {}
    for b in blocks:
        loc = b.get("loc") or {}
        slide = loc.get("slide")
        if not isinstance(slide, int):
            continue
        if b.get("type") in {"bullet", "title"} and b.get("text"):
            out[slide] = (out.get(slide, "") + " " + b["text"]).strip()
    return out


def run_alignment(speech_blocks: list[dict[str, Any]], slide_texts: dict[int, str],
                  notes: list[dict[str, Any]], *, lecture: int | None = None,
                  forced_tier: str | None = None) -> Alignment:
    segs = [Segment(block=b["id"], start=float(b["loc"]["t"][0]), end=float(b["loc"]["t"][1]),
                    text=b.get("text") or "")
            for b in speech_blocks]

    anchors = collect_anchors(notes, slide_texts)
    tier = forced_tier or decide_tier({}, bool(segs), len(anchors))

    if tier == "C" or not segs:
        return Alignment(lecture=lecture, tier="C", segments=segs,
                         notes=["无音频或已配置跳过对齐"])
    if tier == "A" and anchors:
        al = align_by_anchors(segs, anchors, sorted(slide_texts))
    else:
        al = align_by_dp(segs, slide_texts, anchors)
        if not anchors and not slide_texts:
            al.notes.append("没有幻灯片文本，无法对齐")
    al.lecture = lecture
    al.notes.extend(_tier_notes(tier, anchors))
    return al


def _tier_notes(tier: str, anchors: list[tuple[float, int]]) -> list[str]:
    if tier == "A":
        return [f"使用 {len(anchors)} 个锚点插值"]
    if tier == "B":
        return ["无锚点，使用词面匹配 + 单调约束；低置信项需人工确认"]
    return []


def apply_to_blocks(speech_blocks: list[dict[str, Any]], alignment: Alignment) -> int:
    """Give each speech block a slide locator so units can group it."""
    by_id = {s.block: s for s in alignment.segments}
    moved = 0
    for b in speech_blocks:
        seg = by_id.get(b.get("id"))
        if seg is None or seg.slide is None:
            continue
        t = b.get("loc", {}).get("t")
        b["extra"] = {**(b.get("extra") or {}), "t": t}
        b["loc"] = {"slide": seg.slide, "t": t}
        moved += 1
    return moved


def save(workspace: Path, alignment: Alignment, name: str) -> Path:
    out_dir = workspace / ".study" / "aligned"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.align.json"
    path.write_text(json.dumps(alignment.to_json(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    if any(s.unconfirmed for s in alignment.segments):
        confirm = [s for s in alignment.segments if s.unconfirmed]
        lines = ["# 低置信度对齐项，确认后把 slide 填进 answer 字段",
                 "# 保存为 " + f"{name}.confirm.yaml，然后 `study align --confirm`"]
        for s in confirm:
            lines += [
                f"- block: {s.block}",
                f"  t: [{s.start}, {s.end}]",
                f"  text: {s.text[:60]}",
                f"  auto: {s.slide}",
                f"  confidence: {round(s.confidence, 3)}",
                "  answer:",
            ]
        (out_dir / f"{name}.confirm.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
