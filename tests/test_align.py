"""Audio alignment: tiers, SRT parsing, anchors and monotone DP (docs/alignment.md)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from course_content_organization.align import (
    Segment,
    align_by_anchors,
    align_by_dp,
    apply_to_blocks,
    decide_tier,
    run_alignment,
)
from course_content_organization.cli import main
from course_content_organization.ingest.media_adapter import MediaAdapter, parse_srt

SRT = """1
00:00:00,000 --> 00:00:08,500
今天我们讲树，树是 n 个元素的有限集

2
00:00:08,500 --> 00:00:20,000
n 等于零的时候是空树，这个要记住

3
00:00:20,000 --> 00:00:32,000
示例条目戊

4
00:00:32,000 --> 00:00:45,500
第 i 层至多有 2 的 i 减 1 次方个元素
"""


def test_parse_srt_reads_cues():
    cues = parse_srt(SRT)
    assert len(cues) == 4
    assert cues[0]["start"] == 0.0 and cues[0]["end"] == 8.5
    assert "空树" in cues[1]["text"]
    assert all(c["end"] > c["start"] for c in cues)


def test_parse_srt_tolerates_indexes_and_vtt():
    vtt = "WEBVTT\n\n00:01:02.500 --> 00:01:04.000\n<v 讲师>空树</v>\n"
    cues = parse_srt(vtt)
    assert cues == [{"start": 62.5, "end": 64.0, "text": "空树"}]


def test_tier_decision():
    assert decide_tier({}, False, 0) == "C"
    assert decide_tier({}, True, 0) == "B"
    assert decide_tier({}, True, 1) == "A"
    assert decide_tier({}, True, 5) == "A"


def test_tier_a_interpolates_between_anchors():
    segs = [Segment(block=f"s{i}", start=i * 10.0, end=i * 10.0 + 9, text="")
            for i in range(4)]
    anchors = [(0.0, 1), (30.0, 4)]
    al = align_by_anchors(segs, anchors, [1, 2, 3, 4])
    assert al.tier == "A"
    assert [s.slide for s in al.segments] == [1, 2, 3, 4]
    assert all(s.method == "anchor" for s in al.segments)


def test_tier_b_dp_is_monotone_and_uses_content():
    speech = [
        {"id": "a", "text": "树是 n 个元素的有限集，空树也是树",
         "loc": {"t": [0.0, 10.0]}},
        {"id": "b", "text": "示例条目戊",
         "loc": {"t": [10.0, 20.0]}},
        {"id": "c", "text": "第 i 层至多有 2 的 i 减 1 次方个元素",
         "loc": {"t": [20.0, 30.0]}},
    ]
    slides = {
        1: "树是 n 个元素的有限集",
        2: "示例条目戊",
        3: "第 i 层至多有 2^(i-1) 个元素",
        4: "示例条目己",
    }
    al = run_alignment(speech, slides, [])
    assert al.tier == "B"
    assigned = [s.slide for s in al.segments]
    assert assigned == [1, 2, 3], assigned
    assert assigned == sorted(a for a in assigned if a is not None), "slide index must not go back"


def test_unrelated_speech_can_be_left_unassigned():
    segs = [Segment(block="x", start=0, end=5, text="好，我们休息十分钟再继续")]
    al = align_by_dp(segs, {1: "示例条目戊", 2: "示例小节一"})
    assert al.tier == "B"
    assert al.segments[0].slide is None or al.segments[0].confidence < 0.9


def test_apply_to_blocks_moves_time_into_extra():
    blocks = [{"id": "a", "type": "speech", "loc": {"t": [0.0, 5.0]}, "text": "x"}]
    al = run_alignment(blocks, {1: "示例小节一"}, [])
    moved = apply_to_blocks(blocks, al)
    assert moved == 1
    assert blocks[0]["loc"]["slide"] == 1
    assert blocks[0]["extra"]["t"] == [0.0, 5.0]


def test_media_adapter_uses_sidecar_transcript(tmp_path: Path):
    audio = tmp_path / "第6章 录音.m4a"
    audio.write_bytes(b"\x00" * 64)
    audio.with_suffix(".srt").write_text(SRT, encoding="utf-8")

    ctx = type("Ctx", (), {"assets_dir": tmp_path / "assets", "config": None,
                           "workspace": tmp_path})()
    result = MediaAdapter().ingest(audio, ctx)
    speech = [b for b in result.blocks if b.type == "speech"]
    assert len(speech) == 4
    assert speech[0].loc["t"] == [0.0, 8.5]
    assert result.capabilities["transcript"] == "第6章 录音.srt"


def test_pipeline_with_audio_puts_speech_in_the_unit(tmp_path, sample_pptx):
    ws = tmp_path / "ws"
    main(["--workspace", str(ws), "init", "--course", "示例课程"])
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / "inputs" / sample_pptx.name).write_bytes(sample_pptx.read_bytes())

    audio = ws / "inputs" / "第6章 录音.m4a"
    audio.write_bytes(b"\x00" * 64)
    audio.with_suffix(".srt").write_text(SRT, encoding="utf-8")

    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"] = [{"number": 6, "title": "示例主题 A",
                                  "sources": [f"inputs/{sample_pptx.name}",
                                              f"inputs/{audio.name}"]}]
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    main(["--workspace", str(ws), "build"])
    book = (ws / ".study" / "out" / "book.md").read_text(encoding="utf-8")
    assert "讲解" in book, "aligned speech must reach the document"
    assert "示例条目戊" in book

    align_file = ws / ".study" / "aligned" / "s06.align.json"
    assert align_file.exists()
    data = json.loads(align_file.read_text(encoding="utf-8"))
    assert data["tier"] in {"A", "B"}
    assert data["segments"]


def test_c_tier_writes_nothing_to_swallow_the_absence(tmp_path, sample_pptx):
    ws = tmp_path / "ws"
    main(["--workspace", str(ws), "init", "--course", "示例课程"])
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / "inputs" / sample_pptx.name).write_bytes(sample_pptx.read_bytes())
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"] = [{"number": 6, "title": "示例主题 A",
                                  "sources": [f"inputs/{sample_pptx.name}"]}]
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    main(["--workspace", str(ws), "build"])
    assert not (ws / ".study" / "aligned" / "s06.align.json").exists()
