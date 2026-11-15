"""Command line interface (docs/cli.md)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from . import PIPELINE_VERSION, SCHEMA_VERSION
from .config import CONFIG_NAME, Config
from .gates import GateReport, run_gates
from .ids import src_key
from .ir import Block, read_jsonl, sha256_file, write_jsonl
from .knowledge import Unit, draft_units, load_units, save_unit
from .outline import build_outline, load_outline, save_outline
from .render import load_glossary, load_notes, render_document
from .figures import add_figure, load_figures, request_figure
from .templates import TemplateSet
from .gaps import apply_answers, fill_gap, load_answers, open_gaps, register_image_gaps
from .review import apply as review_apply
from .review import prepare as review_prepare
from . import align as align_mod
from . import compose as compose_mod
from . import exporters
from . import latex as latex_mod
from . import noise
from . import remediate
from .review import apply_l3 as review_apply_l3
from .review import prepare_l3 as review_prepare_l3
from .review import retag_origins as review_retag
from .review import stats_from_answers as review_stats
from .state import State

EXIT_OK, EXIT_GATE, EXIT_USAGE, EXIT_INPUT, EXIT_INTERNAL, EXIT_CONFIRM = 0, 1, 2, 3, 4, 5


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _workspace(args) -> Config:
    try:
        return Config.find(Path(args.workspace or "."))
    except FileNotFoundError as exc:
        print(f"error: {exc}  (run `study init` first)", file=sys.stderr)
        raise SystemExit(EXIT_INPUT)


def _paths(cfg: Config) -> dict[str, Path]:
    ws = cfg.workspace
    return {
        "ws": ws,
        "inputs": ws / "inputs",
        "study": ws / ".study",
        "sources": ws / ".study" / "sources",
        "assets": ws / ".study" / "assets",
        "units": ws / ".study" / "knowledge" / "units",
        "out": ws / ".study" / "out",
        "review": ws / ".study" / "review",
        "gaps": ws / ".study" / "gaps",
        "patches": ws / ".study" / "patches",
    }


SIDECAR_SUFFIXES = {".srt", ".vtt"}


def _input_files(cfg: Config) -> list[Path]:
    inputs = cfg.workspace / "inputs"
    if not inputs.exists():
        return []
    return sorted(
        p for p in inputs.iterdir()
        if p.is_file() and not p.name.startswith(".")
        and p.suffix.lower() not in SIDECAR_SUFFIXES  # consumed by the media adapter
    )


def _src_to_lecture(cfg: Config, files: list[Path]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for f in files:
        lec = cfg.lecture_for_source(f.name)
        no = Config.lecture_no(lec) if lec else None
        if no is not None:
            mapping[f.name] = int(no)
            continue
        from .ids import lecture_from_name

        n = lecture_from_name(f.name)
        if n is not None:
            mapping[f.name] = n
    return mapping


def _load_blocks(p: dict[str, Path]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    if not p["sources"].exists():
        return out
    for f in sorted(p["sources"].glob("*.jsonl")):
        src = None
        blocks = []
        for b in read_jsonl(f):
            blocks.append(b)
            if src is None and b.get("src"):
                src = b["src"]
        if src:
            out[src] = blocks
    return out


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    ws = Path(args.workspace or ".").resolve()
    cfg_path = ws / CONFIG_NAME
    if cfg_path.exists() and not args.force:
        print(f"error: {CONFIG_NAME} already exists", file=sys.stderr)
        return EXIT_USAGE
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / ".study").mkdir(exist_ok=True)
    data = {
        "schema_version": SCHEMA_VERSION,
        "course": {"name": args.course or "untitled", "lang": args.lang or "zh", "lectures": []},
        "render": {"profile": "learn", "scope": "semester",
                   "source_references": "none", "template": "templates/learn/md"},
    }
    cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"initialized workspace at {ws}")
    print("next: put sources in inputs/, declare lectures in study.config.yaml, then `study build`")
    return EXIT_OK


def cmd_probe(args) -> int:
    cfg = _workspace(args)
    p = _paths(cfg)
    from .ingest import adapter_for

    caps: dict[str, Any] = {"files": {}, "anchors": {"user_provided": 0, "detected_page_turns": 0}}
    for f in _input_files(cfg):
        ad = adapter_for(f)
        if ad is None:
            continue
        entry = ad().probe(f)
        caps["files"][f.name] = {"adapter": ad.name, **entry}
        if ad.name == "notes":
            caps["anchors"]["user_provided"] += int(entry.get("items") or 0)

    total_slides = sum(v.get("slides", 0) for v in caps["files"].values())
    has_audio = any(v.get("adapter") == "media" for v in caps["files"].values())
    caps["slides"] = {"count": total_slides}
    caps["audio"] = {"present": has_audio}
    caps["tier_hint"] = "C" if not has_audio else ("A" if caps["anchors"]["user_provided"] else "B")

    out = p["study"] / "probe.json"
    out.write_text(json.dumps(caps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state = State.load(cfg.workspace)
    state.mark("probe", "done", key=state.inputs_hash())
    state.record_inputs(_input_files(cfg))
    state.save()

    if args.json:
        print(json.dumps(caps, ensure_ascii=False))
    else:
        print(f"tier hint: {caps['tier_hint']}  slides: {total_slides}  files: {len(caps['files'])}")
    return EXIT_OK


def cmd_ingest(args) -> int:
    cfg = _workspace(args)
    p = _paths(cfg)
    from .ingest import adapter_for

    files = _input_files(cfg)
    if not files:
        print("error: no files in inputs/", file=sys.stderr)
        return EXIT_INPUT

    p["sources"].mkdir(parents=True, exist_ok=True)
    p["assets"].mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, Any]] = []
    produced = 0
    used_names: dict[str, int] = {}

    for f in files:
        ad = adapter_for(f)
        if ad is None:
            errors.append({"file": f.name, "stage": "dispatch", "severity": "warning",
                           "msg": "无可用适配器，已跳过"})
            continue
        if args.only and ad.name not in args.only.split(","):
            continue
        ctx = type("Ctx", (), {"assets_dir": p["assets"], "config": cfg})()
        try:
            result = ad().ingest(f, ctx)
        except Exception as exc:  # noqa: BLE001
            errors.append({"file": f.name, "stage": f"{ad.name}/ingest", "severity": "error",
                           "msg": str(exc)})
            continue
        # two sources may map to the same readable key (same lecture number): never
        # let one overwrite the other, and keep the readable name for the first
        base = f"{ad.name}-{src_key(f.name, ad.name)}"
        used_names[base] = used_names.get(base, 0) + 1
        if used_names[base] > 1:
            import hashlib as _hashlib
            base = f"{base}-{_hashlib.sha256(f.name.encode('utf-8')).hexdigest()[:4]}"
        target = p["sources"] / f"{base}.jsonl"
        n = write_jsonl(target, result.blocks)
        produced += n
        for w in result.warnings:
            w = dict(w)
            w.setdefault("file", f.name)
            w.setdefault("severity", "warning")
            errors.append(w)

    (p["study"] / "errors.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in errors), encoding="utf-8")

    state = State.load(cfg.workspace)
    state.record_inputs(files)
    state.mark("ingest", "done", key=state.inputs_hash(), warnings=len(errors))
    state.save()

    blockers = [e for e in errors if e.get("severity") in ("error", "blocker")]
    print(f"ingested {produced} blocks from {len(files)} files, {len(errors)} warnings")
    if blockers:
        for e in blockers[:5]:
            print(f"  error: {e.get('file')}: {e.get('msg')}", file=sys.stderr)
        return EXIT_INPUT
    return EXIT_OK


def cmd_build(args) -> int:
    cfg = _workspace(args)
    p = _paths(cfg)
    state = State.load(cfg.workspace)

    rc = cmd_ingest(argparse.Namespace(**{**vars(args), "only": None}))
    if rc not in (EXIT_OK,):
        return rc

    cfg = _workspace(args)  # reload: config may not change, but keeps flow obvious
    state = State.load(cfg.workspace)  # ingest wrote its own stage; do not clobber it
    files = _input_files(cfg)
    blocks_by_src = _load_blocks(p)
    if not blocks_by_src:
        print("error: no IR produced", file=sys.stderr)
        return EXIT_INPUT

    src_to_lecture = _src_to_lecture(cfg, files)
    lecture_titles: dict[int, str] = {}
    for lec in cfg.lectures():
        no = Config.lecture_no(lec)
        if no is not None:
            lecture_titles[int(no)] = lec.get("title") or f"第 {no} 讲"

    # ---- model gaps: answers are attached, missing ones are registered ----
    answers = load_answers(cfg.workspace)
    flat_blocks = [b for bs in blocks_by_src.values() for b in bs]
    applied = apply_answers(flat_blocks, answers)
    if applied:
        print(f"applied {applied} gap answers")

    # ---- alignment (A/B/C tier, deterministic) ----
    aligned = _align_workspace(cfg, p, blocks_by_src)

    # ---- units (deterministic draft) ----
    existing_units = {u.id: u for u in load_units(p["units"])} if p["units"].exists() else {}
    # a human-renamed unit keeps its semantic id; the draft it replaces announces the
    # old machine id in `aliases`, so re-running must not resurrect a second copy
    alias_index: dict[str, str] = {}
    for u in existing_units.values():
        for a in u.aliases:
            alias_index[a] = u.id

    drafted = draft_units(blocks_by_src, src_to_lecture=src_to_lecture)
    prune_report = noise.prune_units(drafted)
    if prune_report["dropped"]:
        print(f"pruned {prune_report['dropped']} 条非教学内容"
              f"（{prune_report['units']} 个知识点）")
    written, preserved = 0, 0
    for u in drafted:
        if u.id in alias_index:
            u.id = alias_index[u.id]
            if u.id in existing_units:
                preserved += 1
                continue
        old = existing_units.get(u.id)
        if old is not None and not old.draft and old.content_fingerprint != u.content_fingerprint:
            # human-edited unit: never overwrite silently (docs/state.md §8.1)
            (p["units"] / f"{u.id}.generated.md").write_text(u.to_markdown(), encoding="utf-8")
            preserved += 1
            continue
        if old is not None:
            changed = old.content_fingerprint != u.content_fingerprint
            u.revision = old.revision + (1 if changed else 0)
        save_unit(p["units"], u)
        written += 1

    units = load_units(p["units"])
    if review_retag(cfg.workspace, p["units"], units):
        units = load_units(p["units"])  # review metadata survives the rebuild
    new_gaps = register_image_gaps(cfg.workspace, flat_blocks, units)
    pending = open_gaps(cfg.workspace)
    state.mark("units", "partial" if pending else "done", key=state.inputs_hash(),
               count=len(units), preserved=preserved, pending_gaps=len(pending))
    state.save()

    # ---- outline ----
    existing_outline = load_outline(cfg.workspace)
    if existing_outline and alias_index:
        # a renamed unit keeps its hand-placed position instead of jumping to the end
        for part in existing_outline.get("parts", []):
            for sec in part.get("sections", []):
                sec["units"] = [alias_index.get(u, u) for u in sec.get("units", [])]
    outline, conflicts = build_outline(
        units, lecture_titles=lecture_titles, src_to_lecture=src_to_lecture,
        existing=existing_outline if existing_outline and not args.no_merge else None,
    )
    save_outline(cfg.workspace, outline)
    if conflicts:
        print("outline conflicts: " + "; ".join(conflicts), file=sys.stderr)

    # ---- render + gates ----
    rc = _do_render(cfg, args, state)
    return rc


def _align_workspace(cfg: Config, p: dict[str, Path], blocks_by_src: dict[str, list[dict[str, Any]]],
                     *, tier: str | None = None) -> dict[str, Any]:
    """Align speech to slides when audio was ingested; annotate blocks in place."""
    speech = [b for bs in blocks_by_src.values() for b in bs if b.get("type") == "speech"]
    if not speech:
        return {"tier": "C", "segments": 0}
    slides_blocks = [b for bs in blocks_by_src.values() for b in bs
                     if b.get("type") in {"bullet", "title"} and (b.get("loc") or {}).get("slide")]
    slide_texts = align_mod.slide_texts_from_blocks(slides_blocks)
    notes = _prepare_notes(p, [b for bs in blocks_by_src.values() for b in bs],
                           load_units(p["units"]) if p["units"].exists() else [])
    lecture = None
    srcs = {b.get("src") for b in speech}
    for src in srcs:
        lec = cfg.lecture_for_source(src or "")
        if lec:
            lecture = Config.lecture_no(lec)
    al = align_mod.run_alignment(speech, slide_texts, notes, lecture=lecture, forced_tier=tier)
    moved = align_mod.apply_to_blocks(speech, al)
    name = f"s{lecture:02d}" if lecture else "session"
    align_mod.save(cfg.workspace, al, name)
    state = State.load(cfg.workspace)
    state.mark("align", "done", key=state.inputs_hash(), tier=al.tier, moved=moved,
               unconfirmed=sum(1 for s in al.segments if s.unconfirmed))
    state.save()
    print(f"aligned: tier {al.tier}, {moved}/{len(speech)} segments, confidence {al.confidence}")
    if any(s.unconfirmed for s in al.segments):
        print(f"  低置信度 {sum(1 for s in al.segments if s.unconfirmed)} 条待确认")
    return {"tier": al.tier, "segments": len(al.segments), "confidence": al.confidence}


def _gap_counts(workspace: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in open_gaps(workspace):
        try:
            kind = json.loads(path.read_text(encoding="utf-8")).get("kind", "unknown")
        except Exception:
            kind = "unknown"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _finding_counts(workspace: Path) -> dict[str, dict[str, int]]:
    path = workspace / ".study" / "review" / "findings.jsonl"
    out: dict[str, dict[str, int]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            f = json.loads(line)
        except Exception:
            continue
        out.setdefault(f.get("level", "info"), {})
        gate = f.get("gate", "?")
        out[f.get("level", "info")][gate] = out[f.get("level", "info")].get(gate, 0) + 1
    return out


def _status_report(cfg: Config, p: dict[str, Path]) -> list[str]:
    state = State.load(cfg.workspace)
    lines = ["", "── 当前状态 ──"]
    for stage in ("ingest", "align", "units", "outline", "render", "gates"):
        info = state.get_stage(stage)
        if not info:
            continue
        detail = info.get("tier") or info.get("count") or ""
        lines.append(f"  {stage:8} {info.get('status', '?'):8} {detail}")

    gaps = _gap_counts(cfg.workspace)
    if gaps:
        lines.append("  待填充缺口：" + "，".join(f"{k}×{v}" for k, v in sorted(gaps.items())))
    findings = _finding_counts(cfg.workspace)
    if findings:
        parts = []
        for level in ("blocker", "major", "minor", "info"):
            if level in findings:
                parts.append(f"{level}×{sum(findings[level].values())}")
        lines.append("  待处理问题：" + "，".join(parts))

    out = p["out"]
    produced = sorted(f for f in out.glob("book.*") if f.is_file()) if out.exists() else []
    if produced:
        lines.append("  产物：" + "，".join(f"{f.name}({f.stat().st_size // 1024}KB)"
                                          for f in produced))
    return lines


def cmd_auto(args) -> int:
    """One command for a full cycle, ending with what is still missing."""
    cfg = _workspace(args)
    p = _paths(cfg)

    # cmd_build and cmd_ingest read args by name; give them exactly what they expect
    build_args = argparse.Namespace(
        workspace=args.workspace, json=getattr(args, "json", False),
        no_merge=False, no_interactive=True, profile=args.profile,
        format=args.format, tex_engine=args.tex_engine,
        source_references=None, only=None, force=False,
    )
    rc = cmd_build(build_args)

    units = load_units(p["units"]) if p["units"].exists() else []
    if units and not args.skip_review:
        blocks_by_src = _load_blocks(p)
        l2 = review_prepare(cfg.workspace, units, blocks_by_src, limit=args.l2_limit)
        l3 = review_prepare_l3(cfg.workspace, units, limit=args.l3_limit,
                               sample_ratio=args.sample)
        if l2 or l3:
            print(f"审查任务：L2 {len(l2)} 个，L3 {len(l3)} 个（填入 .study/gaps/answers/）")

    for line in _status_report(cfg, p):
        print(line)

    pending = open_gaps(cfg.workspace)
    if pending:
        print(f"\n下一步：填写 {len(pending)} 个缺口（`study gaps list`），"
              f"然后 `study review apply` 与 `study auto`")
        return EXIT_CONFIRM
    return rc


def _export(cfg: Config, p: dict[str, Path], fmt: str, units: list, outline: dict,
            rendered, profile: str, notes: list, *, args: str | None = None) -> list[Path]:
    """Write one output format; return the files produced (empty if it failed)."""
    title = cfg.get("course", "name", default="讲义")
    out = p["out"]

    if fmt in ("md", ""):
        return []

    if fmt == "txt":
        path = out / "book.txt"
        path.write_text(exporters.to_text(rendered.text), encoding="utf-8", newline="\n")
        print(f"txt  -> {path}")
        return [path]

    if fmt in ("docx", "pptx"):
        items = exporters.document_items(units, outline, profile=profile,
                                         templates=_templates(cfg))
        path = out / f"book.{fmt}"
        try:
            if fmt == "docx":
                exporters.to_docx(items, path, title=title, assets_dir=p["assets"])
            else:
                exporters.to_pptx(items, path, title=title, assets_dir=p["assets"])
        except ImportError as exc:
            print(f"{fmt} 导出失败：{exc}（uv pip install study-harness[{fmt}]）", file=sys.stderr)
            return []
        print(f"{fmt:4} -> {path}")
        return [path]

    if fmt in ("tex", "pdf"):
        # LaTeX has its own manifest: markdown layout and print layout evolve separately
        tex_tpl = TemplateSet.load(cfg.workspace / "templates" / profile / "tex")
        tex = latex_mod.build_tex(
            units, outline, title=title, profile=profile, templates=tex_tpl,
            assets_dir=p["assets"], notes=notes,
            remedial=remediate.load(cfg.workspace), figures=load_figures(cfg.workspace),
        )
        tex_path = out / "book.tex"
        tex_path.write_text(tex, encoding="utf-8", newline="\n")
        print(f"tex  -> {tex_path}")
        produced = [tex_path]
        if fmt == "pdf":
            pdf, logs = latex_mod.compile_pdf(tex_path, engine=args)
            for line in logs:
                print(f"  {line}")
            if pdf is None:
                print("未生成 PDF：.tex 已保留，可自行编译或检查上方日志", file=sys.stderr)
            else:
                print(f"pdf  -> {pdf}")
                produced.append(pdf)
        return produced

    print(f"未知输出格式：{fmt}", file=sys.stderr)
    return []


def _templates(cfg: Config) -> TemplateSet:
    rel = cfg.get("render", "template", default=None)
    return TemplateSet.load((cfg.workspace / rel) if rel else None)


def _do_render(cfg: Config, args, state: State) -> int:
    p = _paths(cfg)
    units = load_units(p["units"])
    outline = load_outline(cfg.workspace)
    if outline is None:
        print("error: no outline; run `study build`", file=sys.stderr)
        return EXIT_INPUT

    blocks: list[dict[str, Any]] = [b for bs in _load_blocks(p).values() for b in bs]
    mode = getattr(args, "source_references", None) or cfg.get("render", "source_references",
                                                              default="none")
    profile = getattr(args, "profile", None) or cfg.get("render", "profile", default="learn")

    notes = _prepare_notes(p, blocks, units)
    rendered = render_document(
        units, outline, profile=profile, title=cfg.get("course", "name", default="讲义"),
        assets_src=p["assets"], assets_dst=p["out"] / "book.assets",
        notes=notes, glossary=load_glossary(cfg.workspace),
        figures=load_figures(cfg.workspace),
        templates=_templates(cfg),
        remedial=remediate.load(cfg.workspace),
    )

    p["out"].mkdir(parents=True, exist_ok=True)
    (p["out"] / "book.md").write_text(rendered.text, encoding="utf-8", newline="\n")

    fmt = (getattr(args, "format", None) or cfg.get("render", "format", default="md") or "md").lower()
    formats = ["md", "txt", "tex", "pdf", "docx", "pptx"] if fmt == "all" else [fmt]
    produced: list[Path] = [p["out"] / "book.md"]
    for one in formats:
        produced += _export(cfg, p, one, units, outline, rendered, profile, notes,
                            args=getattr(args, "tex_engine", None))

    p["review"].mkdir(parents=True, exist_ok=True)
    (p["review"] / "locators.md").write_text(rendered.audit_text, encoding="utf-8", newline="\n")
    (p["out"] / "meta.json").write_text(json.dumps({
        "pipeline_version": PIPELINE_VERSION,
        "profile": profile,
        "scope": "semester",
        "source_references": mode,
        "unit_count": len(units),
        "template_hash": _templates(cfg).hash,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report: GateReport = run_gates(units=units, outline=outline, blocks=blocks,
                                   rendered_text=rendered.text, source_mode=mode,
                                   figures=load_figures(cfg.workspace))
    findings_path = p["review"] / "findings.jsonl"
    findings_path.write_text(
        "".join(json.dumps(f.to_dict(), ensure_ascii=False) + "\n" for f in report.findings),
        encoding="utf-8")

    state.mark("render", "done", key=state.inputs_hash())
    state.mark("gates", "failed" if report.failed else "done",
               key=state.inputs_hash())
    state.save()

    print(f"rendered {len(units)} units -> {p['out'] / 'book.md'}")
    pending = open_gaps(cfg.workspace)
    if pending:
        print(f"gaps: {len(pending)} 待填充（`study gaps list`），未填充时相关内容不进入正文")
    if report.findings:
        print("gates: " + ", ".join(f"{k}={v}" for k, v in sorted(report.counts.items())))
        for f in report.findings:
            if f.level in ("blocker", "major"):
                loc = f" [{f.unit}]" if f.unit else ""
                print(f"  {f.level}: {f.gate}{loc} — {f.message}")
    return EXIT_GATE if report.failed else EXIT_OK


def _prepare_notes(p: dict[str, Path], blocks: list[dict[str, Any]], units: list[Unit]) -> list[dict[str, Any]]:
    """Promote note blocks to an overlay, mapping slide anchors onto units."""
    p["patches"].mkdir(parents=True, exist_ok=True)
    notes_path = p["patches"] / "notes.yaml"
    if notes_path.exists():
        data = yaml.safe_load(notes_path.read_text(encoding="utf-8")) or []
        return data if isinstance(data, list) else data.get("notes", [])

    raw = [b for b in blocks if b.get("type") == "note"]
    slide_to_units: dict[int, list[str]] = {}
    for u in units:
        for e in u.evidence:
            slide = (e.get("loc") or {}).get("slide")
            if isinstance(slide, int):
                slide_to_units.setdefault(slide, []).append(u.id)

    out: list[dict[str, Any]] = []
    for b in raw:
        loc = b.get("loc") or {}
        entry: dict[str, Any] = {"text": b.get("text"), "kind": b.get("kind") or "global"}
        if "unit" in loc:
            entry["loc"] = {"unit": loc["unit"]}
        elif "slide" in loc:
            targets = slide_to_units.get(int(loc["slide"]), [])
            if targets:
                entry["loc"] = {"unit": targets[0], "slide": int(loc["slide"])}
            else:
                entry["loc"] = {"slide": int(loc["slide"])}
        out.append(entry)

    notes_path.write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out


def cmd_render(args) -> int:
    cfg = _workspace(args)
    state = State.load(cfg.workspace)
    return _do_render(cfg, args, state)


def cmd_align(args) -> int:
    cfg = _workspace(args)
    p = _paths(cfg)
    blocks_by_src = _load_blocks(p)
    if not blocks_by_src:
        print("error: no IR; run `study ingest` first", file=sys.stderr)
        return EXIT_INPUT
    info = _align_workspace(cfg, p, blocks_by_src, tier=args.tier)
    if info["tier"] == "C":
        print("没有可对齐的语音；C 档下游不依赖对齐")
        return EXIT_OK
    return EXIT_OK


def cmd_outline(args) -> int:
    cfg = _workspace(args)
    outline = load_outline(cfg.workspace)
    if outline is None:
        print("error: no outline", file=sys.stderr)
        return EXIT_INPUT
    if args.action == "show":
        if args.json:
            print(json.dumps(outline, ensure_ascii=False, indent=2))
        else:
            for part in outline["parts"]:
                print(f"[{part['kind']}] {part.get('title')}")
                for s in part.get("sections", []):
                    print(f"  - {s['title']}: {len(s['units'])} units")
        return EXIT_OK
    if args.action == "move":
        target = (args.to or "").split("/")
        if len(target) != 2:
            print("error: --to <lecture>/<section> required", file=sys.stderr)
            return EXIT_USAGE
        moved = False
        for part in outline["parts"]:
            for s in part.get("sections", []):
                if args.unit in s["units"]:
                    s["units"].remove(args.unit)
                    moved = True
        if not moved:
            print(f"error: unit {args.unit} not in outline", file=sys.stderr)
            return EXIT_INPUT
        for part in outline["parts"]:
            if part.get("kind") == "lecture" and str(part.get("no")) == target[0]:
                for i, s in enumerate(part.get("sections", [])):
                    if str(i) == target[1] or s["title"] == target[1]:
                        s["units"].append(args.unit)
                        moved = True
                        break
        save_outline(cfg.workspace, outline)
        print(f"moved {args.unit}")
        return EXIT_OK
    return EXIT_USAGE


def cmd_compose(args) -> int:
    """Prepare or apply the writing stage: drafts -> prose the user asked for."""
    cfg = _workspace(args)
    p = _paths(cfg)
    units = load_units(p["units"]) if p["units"].exists() else []
    if not units:
        print("error: no units; run `study auto` first", file=sys.stderr)
        return EXIT_INPUT

    if args.apply:
        findings, stats = compose_mod.apply(cfg.workspace, p["units"], units)
        _merge_findings(p["review"], findings)
        print(f"已合成 {stats['composed']} 个知识点（跳过 {stats['skipped']}）")
        if findings:
            print(f"{len(findings)} 条记录写入 findings；`study review list`")
        return EXIT_OK

    style = args.style or cfg.get("compose", "style", default="") or ""
    example = args.example or cfg.get("compose", "example", default="") or ""
    if args.style and not cfg.get("compose", "style", default=""):
        # remember it: the style belongs to the workspace, not to one invocation
        data = cfg.data
        data.setdefault("compose", {})
        data["compose"]["style"] = args.style
        cfg.path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                            encoding="utf-8")
        print("style 已写入 study.config.yaml（compose.style）")
    if not style.strip():
        print("error: 缺少写作要求（compose.style）。写作层不预设文风，也不提供样例。\n"
              "  用 --style '<你的要求>' 传入，或写入 study.config.yaml 的 compose.style。",
              file=sys.stderr)
        return EXIT_USAGE

    targets = [u for u in units if u.draft] if not args.all else units
    if args.only:
        targets = [u for u in targets if u.id == args.only]
    created = compose_mod.prepare(cfg.workspace, targets, style=style, example=example,
                                  limit=args.limit)
    print(f"写作任务：{len(created)} 个（.study/gaps/compose-*.json）")
    if created:
        print("填 `.study/gaps/answers/<id>.json` 后执行 `study compose --apply`")
    return EXIT_OK


def cmd_review(args) -> int:
    cfg = _workspace(args)
    p = _paths(cfg)

    if args.action == "prepare":
        units = load_units(p["units"]) if p["units"].exists() else []
        if not units:
            print("error: no units; run `study build` first", file=sys.stderr)
            return EXIT_INPUT
        blocks_by_src = _load_blocks(p)
        created = review_prepare(cfg.workspace, units, blocks_by_src,
                                 only=args.unit, limit=args.limit)
        print(f"L2 gaps: {len(created)} written to .study/gaps/")
        if created:
            print("fill `.study/gaps/answers/<id>.json`，然后 `study review apply`")
        return EXIT_OK

    if args.action == "prepare-l3":
        units = load_units(p["units"]) if p["units"].exists() else []
        if not units:
            print("error: no units; run `study build` first", file=sys.stderr)
            return EXIT_INPUT
        created = review_prepare_l3(cfg.workspace, units, only=args.unit,
                                    limit=args.limit, sample_ratio=args.sample or 0.1)
        print(f"L3 gaps: {len(created)} written（只审公式/数值/补充/重点，其余抽样）")
        print("L3 的审查者必须换模型或换干净上下文；填完 `study review apply`")
        return EXIT_OK

    if args.action == "apply":
        units = load_units(p["units"]) if p["units"].exists() else []
        if not units:
            print("error: no units", file=sys.stderr)
            return EXIT_INPUT
        findings2, stats2 = review_apply(cfg.workspace, p["units"], units)
        findings3, stats3 = review_apply_l3(cfg.workspace, p["units"], load_units(p["units"]))
        findings = findings2 + findings3
        _merge_findings(p["review"], findings)
        state = State.load(cfg.workspace)
        state.data.setdefault("review", {})["l2"] = stats2
        state.data.setdefault("review", {})["l3"] = stats3
        state.save()
        print(f"L2: {stats2['units_reviewed']} units / {stats2['claims']} claims: "
              f"supported={stats2['supported']} derived={stats2['derived']} "
              f"unsupported={stats2['unsupported']}")
        if stats3["units_reviewed"]:
            print(f"L3: {stats3['units_reviewed']} units / {stats3['issues']} issues")
        if findings:
            print(f"{len(findings)} findings written；`study review list`")
        return EXIT_OK

    f = p["review"] / "findings.jsonl"
    items = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()
             if line.strip()] if f.exists() else []

    if args.action == "stats":
        counts: dict[str, int] = {}
        for it in items:
            counts[it["level"]] = counts.get(it["level"], 0) + 1
        payload = {"findings": len(items), "by_level": counts,
                   "l2": review_stats(cfg.workspace)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK

    if not items:
        print("no findings")
        return EXIT_OK
    for it in items:
        if args.level and it["level"] != args.level:
            continue
        loc = f" [{it['unit']}]" if it.get("unit") else ""
        print(f"{it['level']:8} {it['gate']}{loc} — {it['message']}")
    return EXIT_OK


def _merge_findings(review_dir: Path, new: list[dict[str, Any]]) -> None:
    """Append review findings, replacing previous L2 findings for the same unit."""
    review_dir.mkdir(parents=True, exist_ok=True)
    path = review_dir / "findings.jsonl"
    existing: list[dict[str, Any]] = []
    if path.exists():
        existing = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    keep = [f for f in existing if f.get("detected_by") != "l2"]
    merged = keep + [dict(f, detected_by="l2") for f in new]
    path.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in merged),
                    encoding="utf-8")


def cmd_doctor(args) -> int:
    cfg = _workspace(args)
    p = _paths(cfg)
    checks: list[tuple[str, str, str]] = []

    checks.append(("python", "ok", sys.version.split()[0]))
    checks.append(("config", "ok", str(cfg.path)))

    try:
        import pptx  # noqa: F401
        checks.append(("python-pptx", "ok", pptx.__version__))
    except Exception:
        checks.append(("python-pptx", "missing", "pip install python-pptx"))

    for name in ("yaml", "lxml"):
        try:
            __import__(name)
            checks.append((name, "ok", ""))
        except Exception:
            checks.append((name, "missing", ""))

    engine = latex_mod.available_engine()
    checks.append(("latex", "ok" if engine else "missing",
                   engine or "未安装 xelatex/lualatex：只能输出 .tex"))

    if shutil.which("ffmpeg"):
        checks.append(("ffmpeg", "ok", ""))
    else:
        checks.append(("ffmpeg", "missing", "音视频转写不可用（v0.1 不要求）"))

    checks.append(("inputs", "ok" if _input_files(cfg) else "empty",
                   f"{len(_input_files(cfg))} files"))
    checks.append(("state", "ok" if (p["study"] / "state.json").exists() else "none", ""))

    state = State.load(cfg.workspace)
    running = [k for k, v in state.data.get("stages", {}).items() if v.get("status") == "running"]
    if running:
        checks.append(("stale_running", "warning", ", ".join(running)))

    for name, status, detail in checks:
        print(f"{status:8} {name:14} {detail}")
    return EXIT_OK


def cmd_clean(args) -> int:
    cfg = _workspace(args)
    p = _paths(cfg)
    targets: list[Path] = []
    stage = args.stage or "all"
    if stage in ("sources", "all"):
        targets.append(p["sources"])
    if stage in ("aligned", "all"):
        targets.append(p["study"] / "aligned")
    if stage in ("out", "all"):
        targets.append(p["out"])
    if stage == "units":
        targets.append(p["units"])
        if not args.dry_run and not args.yes:
            print("refusing: units/ may contain human edits (use --include-units --yes)",
                  file=sys.stderr)
            return EXIT_USAGE
    for t in targets:
        if t.exists():
            print(("would remove " if args.dry_run else "removing ") + str(t))
            if not args.dry_run:
                shutil.rmtree(t)
    return EXIT_OK


def cmd_gaps(args) -> int:
    cfg = _workspace(args)
    ws = cfg.workspace
    if args.action == "list":
        pending = open_gaps(ws)
        if args.json:
            print(json.dumps([{"id": p.stem} for p in pending], ensure_ascii=False))
            return EXIT_OK
        for path in pending:
            spec = json.loads(path.read_text(encoding="utf-8"))
            print(f"{spec.get('kind','?'):16} {path.stem:36} {spec.get('unit') or '-'}")
        if not pending:
            print("no open gaps")
        else:
            print(f"\n{len(pending)} open；填写 .study/gaps/answers/<id>.json 后重新 build")
        return EXIT_OK

    if args.action == "show":
        path = ws / ".study" / "gaps" / f"{args.target}.json"
        if not path.exists():
            print(f"error: gap {args.target} not found", file=sys.stderr)
            return EXIT_INPUT
        print(path.read_text(encoding="utf-8"))
        return EXIT_OK

    if args.action == "fill":
        if not args.target or not args.file:
            print("error: `gaps fill <id> --file answer.json`", file=sys.stderr)
            return EXIT_USAGE
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        try:
            out = fill_gap(ws, args.target, payload)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_INPUT
        print(f"answer written: {out}")
        return EXIT_OK
    return EXIT_USAGE


def cmd_figure(args) -> int:
    cfg = _workspace(args)
    ws = cfg.workspace

    if args.action == "list":
        figs = load_figures(ws)
        if not figs:
            print("no figures")
            return EXIT_OK
        for fig in figs.values():
            print(f"{fig.provenance:12} {fig.format:8} {fig.id:30} {fig.path.name}")
        return EXIT_OK

    if args.action == "request":
        evidence: list[dict[str, Any]] = []
        units_dir = ws / ".study" / "knowledge" / "units"
        if units_dir.exists():
            for u in load_units(units_dir):
                if u.id == args.target:
                    evidence = u.evidence
                    break
        if not args.target:
            print("error: unit id required", file=sys.stderr)
            return EXIT_USAGE
        path = request_figure(ws, args.target, args.desc or "",
                              preferred=args.format or "mermaid", evidence=evidence)
        print(f"gap written: {path}")
        print("填入图形规格后执行 `study figure add <id> --file <spec>`")
        return EXIT_OK

    if args.action == "add":
        if not args.file:
            print("error: --file required", file=sys.stderr)
            return EXIT_USAGE
        source = Path(args.file)
        if not source.exists():
            print(f"error: {source} not found", file=sys.stderr)
            return EXIT_INPUT
        if not args.target:
            print("error: figure id required", file=sys.stderr)
            return EXIT_USAGE
        fig = add_figure(ws, args.target, source, fmt=args.format,
                         provenance=args.provenance, caption=args.caption)
        print(f"added figure {fig.id} ({fig.provenance}, {fig.format}) -> {fig.path}")
        return EXIT_OK

    return EXIT_USAGE


def cmd_explain(args) -> int:
    """Request, apply or remove an on-demand explanation (docs/architecture.md §13)."""
    cfg = _workspace(args)
    p = _paths(cfg)

    if args.apply:
        written = remediate.apply(cfg.workspace)
        print(f"{len(written)} 个补丁已写入 .study/patches/remedial/")
        for path in written:
            print(f"  {path.name}")
        return EXIT_OK

    if args.remove:
        if not args.unit:
            print("error: --remove 需要知识点 id", file=sys.stderr)
            return EXIT_USAGE
        remediate.remove(cfg.workspace, args.unit)
        print(f"已移除 {args.unit} 的补丁与缺口")
        return EXIT_OK

    if not args.unit:
        print("error: 需要知识点 id", file=sys.stderr)
        return EXIT_USAGE
    units = load_units(p["units"]) if p["units"].exists() else []
    unit = next((u for u in units if u.id == args.unit), None)
    if unit is None:
        print(f"error: 找不到知识点 {args.unit}", file=sys.stderr)
        return EXIT_INPUT
    path = remediate.request(cfg.workspace, unit, focus=args.focus, prereq=args.prereq)
    print(f"gap written: {path}")
    print(f"填 `.study/gaps/answers/{path.stem}.json` 后执行 `study explain --apply`")
    return EXIT_OK


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="study", description="study-harness")
    ap.add_argument("--workspace", default=".", help="workspace directory")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    ap.add_argument("--version", action="version", version=f"study-harness {PIPELINE_VERSION}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a workspace")
    p.add_argument("--course")
    p.add_argument("--lang", default="zh")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="parse inputs into the IR")
    p.add_argument("--only")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("probe", help="report source capabilities")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("build", help="ingest -> units -> outline -> render -> gates")
    p.add_argument("--from")
    p.add_argument("--no-merge", action="store_true", help="ignore existing outline")
    p.add_argument("--no-interactive", action="store_true")
    p.add_argument("--profile")
    p.add_argument("--format", choices=["md", "txt", "tex", "pdf", "docx", "pptx", "all"])
    p.add_argument("--tex-engine", choices=["xelatex", "lualatex", "pdflatex"])
    p.add_argument("--source-references", choices=["none", "footnote", "inline"])
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("render", help="render the document")
    p.add_argument("--profile", choices=["learn", "review"])
    p.add_argument("--source-references", choices=["none", "footnote", "inline"])
    p.add_argument("--format", choices=["md", "txt", "tex", "pdf", "docx", "pptx", "all"])
    p.add_argument("--tex-engine", choices=["xelatex", "lualatex", "pdflatex"])
    p.add_argument("--exclude-patches", action="store_true")
    p.add_argument("--audit-locators", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("auto", help="build + review preparation + status in one go")
    p.add_argument("--profile", choices=["learn", "review"])
    p.add_argument("--format", choices=["md", "txt", "tex", "pdf", "docx", "pptx", "all"])
    p.add_argument("--tex-engine", choices=["xelatex", "lualatex", "pdflatex"])
    p.add_argument("--skip-review", action="store_true", help="do not write L2/L3 tasks")
    p.add_argument("--l2-limit", type=int, default=20)
    p.add_argument("--l3-limit", type=int, default=10)
    p.add_argument("--sample", type=float, default=0.1)
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("compose", help="write teaching prose from drafts (model stage)")
    p.add_argument("--only", help="one unit id")
    p.add_argument("--limit", type=int)
    p.add_argument("--all", action="store_true", help="include already-composed units")
    p.add_argument("--style", help="文风描述，写入 study.config.yaml")
    p.add_argument("--example", help="一段样例文字，作为文风参考")
    p.add_argument("--apply", action="store_true", help="apply filled answers")
    p.set_defaults(func=cmd_compose)

    p = sub.add_parser("align", help="align speech to slides (A/B/C tier)")
    p.add_argument("--tier", choices=["A", "B", "C"])
    p.set_defaults(func=cmd_align)

    p = sub.add_parser("outline", help="inspect or adjust the skeleton")
    p.add_argument("action", choices=["show", "move"])
    p.add_argument("unit", nargs="?")
    p.add_argument("--to")
    p.set_defaults(func=cmd_outline)

    p = sub.add_parser("review", help="prepare / apply / inspect semantic review")
    p.add_argument("action", choices=["list", "stats", "prepare", "prepare-l3", "apply"],
                   nargs="?", default="list")
    p.add_argument("--level")
    p.add_argument("--unit", help="limit prepare to one unit")
    p.add_argument("--limit", type=int, help="cap the number of gaps written")
    p.add_argument("--sample", type=float, help="L3 sampling ratio (default 0.1)")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("gaps", help="list / show / fill model tasks")
    p.add_argument("action", choices=["list", "show", "fill"], nargs="?", default="list")
    p.add_argument("target", nargs="?")
    p.add_argument("--file", help="answer file (fill)")
    p.set_defaults(func=cmd_gaps)

    p = sub.add_parser("explain", help="request / apply / remove an explanation patch")
    p.add_argument("unit", nargs="?")
    p.add_argument("--focus", help="只讲这个疑点")
    p.add_argument("--prereq", action="store_true", help="补前置知识而不是重讲本节")
    p.add_argument("--apply", action="store_true", help="把答案转成补丁")
    p.add_argument("--remove", action="store_true", help="删除该知识点的补丁")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("figure", help="manage figures: extracted, redrawn, synthesized")
    p.add_argument("action", choices=["list", "add", "request"])
    p.add_argument("target", nargs="?", help="figure id (add) or unit id (request)")
    p.add_argument("--file", help="spec file (add)")
    p.add_argument("--format", help="mermaid | dot | svg | tikz | png")
    p.add_argument("--provenance", default="redrawn",
                   choices=["extracted", "redrawn", "synthesized"])
    p.add_argument("--caption")
    p.add_argument("--desc", help="what the figure should show (request)")
    p.set_defaults(func=cmd_figure)

    p = sub.add_parser("doctor", help="self-check workspace and environment")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("clean", help="remove derived data")
    p.add_argument("--stage", choices=["sources", "aligned", "units", "out", "all"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_clean)

    return ap


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page; the pipeline is UTF-8 throughout
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    except BrokenPipeError:
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001
        print(f"internal error: {exc}", file=sys.stderr)
        if "--traceback" in (argv or sys.argv):
            raise
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
