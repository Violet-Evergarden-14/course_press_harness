"""`study auto`: one command, then a clear list of what is still missing."""

from __future__ import annotations

from pathlib import Path

import yaml

from course_content_organization.cli import main


def _ws(tmp_path: Path, pptx: Path) -> Path:
    ws = tmp_path / "ws"
    main(["--workspace", str(ws), "init", "--course", "示例课程"])
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / "inputs" / pptx.name).write_bytes(pptx.read_bytes())
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"] = [{"number": 6, "title": "示例专题",
                                  "sources": [f"inputs/{pptx.name}"]}]
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ws


def test_auto_builds_and_returns_needs_human(tmp_path, sample_pptx, capsys):
    ws = _ws(tmp_path, sample_pptx)
    rc = main(["--workspace", str(ws), "auto"])
    out = capsys.readouterr().out
    assert (ws / ".study" / "out" / "book.md").exists()
    assert "当前状态" in out
    # images without semantics are pending work, so the run is not "done"
    assert rc in (0, 1, 5)
    if rc == 5:
        assert "下一步" in out


def test_auto_registers_review_tasks(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "auto"])
    gaps = {p.stem for p in (ws / ".study" / "gaps").glob("*.json")}
    assert any(g.startswith("l2-") for g in gaps), "L2 tasks must be prepared automatically"
    assert any(g.startswith("l3-") for g in gaps), "L3 tasks must be prepared automatically"


def test_auto_skip_review_leaves_no_review_tasks(tmp_path, sample_pptx):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "auto", "--skip-review"])
    gaps = {p.stem for p in (ws / ".study" / "gaps").glob("*.json")}
    assert not any(g.startswith(("l2-", "l3-")) for g in gaps)


def test_auto_keeps_every_stage_in_state(tmp_path, sample_pptx):
    """ingest writes its own stage; the build that follows must not overwrite it."""
    import json

    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "auto", "--skip-review"])
    state = json.loads((ws / ".study" / "state.json").read_text(encoding="utf-8"))
    assert state["stages"]["ingest"]["status"] == "done"
    assert state["stages"]["units"]["status"] in {"done", "partial"}
    assert "render" in state["stages"]
