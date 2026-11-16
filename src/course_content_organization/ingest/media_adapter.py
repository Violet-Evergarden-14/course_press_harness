"""Audio (and audio-bearing video) adapter (docs/ingest.md §9, docs/alignment.md).

Speech-to-text is itself a model dependency, so it is treated like every other one:
optional, cached, and replaceable. Three paths, in order:

1. a sidecar transcript (`.srt` / `.vtt`) — deterministic, no model, testable;
2. local ASR (`faster-whisper`) when installed, writing the sidecar for next time;
3. nothing available — emit a `transcript` gap instead of silently producing no text.

Video is not processed for frames (docs/architecture.md §3); only its audio track is read.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterator

from ..ids import src_key
from ..ir import Block
from . import register
from .base import Adapter, IngestResult

TIMESTAMP_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})")
CUE_SPLIT_RE = re.compile(r"\n\s*\n")


def _seconds(match: re.Match) -> float:
    return int(match.group("h")) * 3600 + int(match.group("m")) * 60 + int(match.group("s")) + \
        int(match.group("ms").ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[dict[str, Any]]:
    """Parse SRT or WebVTT into [{start, end, text}]. Tolerant by design."""
    cues: list[dict[str, Any]] = []
    for chunk in CUE_SPLIT_RE.split(text.replace("\r\n", "\n").strip()):
        lines = [ln for ln in chunk.split("\n") if ln.strip()]
        if not lines:
            continue
        timing_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if timing_idx is None:
            continue
        left, _, right = lines[timing_idx].partition("-->")
        m1, m2 = TIMESTAMP_RE.search(left), TIMESTAMP_RE.search(right)
        if not m1 or not m2:
            continue
        body = " ".join(lines[timing_idx + 1:]).strip()
        body = re.sub(r"<[^>]+>", "", body)  # vtt inline tags
        if not body:
            continue
        cues.append({"start": round(_seconds(m1), 3), "end": round(_seconds(m2), 3), "text": body})
    return cues


def write_srt(cues: list[dict[str, Any]], path: Path) -> None:
    def fmt(t: float) -> str:
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"

    lines: list[str] = []
    for i, cue in enumerate(cues, start=1):
        lines += [str(i), f"{fmt(cue['start'])} --> {fmt(cue['end'])}", cue["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _ffprobe_duration(path: Path) -> float | None:
    exe = shutil.which("ffprobe") or shutil.which("ffmpeg")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-i", str(path)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
        m = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2})\.(\d{2})", out.stderr)
        if not m:
            return None
        h, mi, s, cs = (int(x) for x in m.groups())
        return round(h * 3600 + mi * 60 + s + cs / 100, 3)
    except Exception:
        return None


@register
class MediaAdapter(Adapter):
    name = "media"
    accepts = (".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma",
               ".mp4", ".mkv", ".webm", ".mov", ".m4v")

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def sidecar_for(path: Path) -> Path | None:
        for ext in (".srt", ".vtt"):
            candidate = path.with_suffix(ext)
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def has_asr() -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:
            return False

    def probe(self, path: Path) -> dict[str, Any]:
        sidecar = self.sidecar_for(path)
        return {
            "present": True,
            "size": path.stat().st_size,
            "duration": _ffprobe_duration(path),
            "transcript": sidecar.name if sidecar else None,
            "asr_available": self.has_asr(),
        }

    # -- ingest ----------------------------------------------------------
    def ingest(self, path: Path, ctx: Any) -> IngestResult:
        res = IngestResult()
        key = src_key(path.name, self.name)
        sidecar = self.sidecar_for(path)

        cues: list[dict[str, Any]] = []
        if sidecar is not None:
            cues = parse_srt(sidecar.read_text(encoding="utf-8", errors="replace"))
            if not cues:
                res.warnings.append({"stage": "media/srt", "severity": "warning",
                                     "msg": f"{sidecar.name} 未解析出字幕条目"})
        elif self.has_asr():
            cues, warn = self._transcribe(path, ctx)
            if warn:
                res.warnings.extend(warn)
        else:
            res.warnings.append({
                "stage": "media/asr", "severity": "warning",
                "msg": "无字幕文件且未安装 faster-whisper，音频内容不可用"})
            self._write_gap(ctx.workspace if hasattr(ctx, "workspace") else None, path)

        for i, cue in enumerate(cues, start=1):
            res.blocks.append(Block(
                id=f"{key}-{i:04d}", src=path.name, type="speech",
                loc={"t": [cue["start"], cue["end"]]},
                text=cue["text"].strip(),
                low_conf=bool(cue.get("low_conf", False)),
            ))

        res.capabilities = self.probe(path)
        res.capabilities["cues"] = len(res.blocks)
        return res

    def _transcribe(self, path: Path, ctx: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        from faster_whisper import WhisperModel  # lazy: optional dependency

        wav = Path(ctx.assets_dir) / f"{hashlib.sha256(str(path).encode()).hexdigest()[:8]}.wav"
        warnings: list[dict[str, Any]] = []
        if not wav.exists():
            try:
                subprocess.run(
                    [shutil.which("ffmpeg") or "ffmpeg", "-y", "-i", str(path), "-ac", "1",
                     "-ar", "16000", str(wav)],
                    capture_output=True, timeout=1800, check=True,
                    text=True, encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                warnings.append({"stage": "media/ffmpeg", "severity": "error", "msg": str(exc)})
                return [], warnings

        model_name = getattr(getattr(ctx, "config", None), "get", lambda *a, **k: None)(
            "alignment", "asr_model", default="small") or "small"
        model = WhisperModel(model_name, device="auto", compute_type="int8")
        segments, _info = model.transcribe(str(wav), vad_filter=True, word_timestamps=True)

        cues: list[dict[str, Any]] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            cues.append({
                "start": round(float(seg.start), 3),
                "end": round(float(seg.end), 3),
                "text": text,
                "low_conf": bool(getattr(seg, "avg_logprob", 0) < -1.0),
            })
        # cache next to the audio: deterministic and reusable without re-running ASR
        try:
            write_srt(cues, path.with_suffix(".srt"))
            warnings.append({"stage": "media/asr", "severity": "info",
                             "msg": f"转写结果已缓存为 {path.with_suffix('.srt').name}"})
        except Exception:
            pass
        return cues, warnings

    def _write_gap(self, workspace: Path | None, path: Path) -> None:
        if workspace is None:
            return
        gaps = Path(workspace) / ".study" / "gaps"
        gaps.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": f"transcript-{path.stem}".replace(" ", "-").lower(),
            "kind": "transcript",
            "source": path.name,
            "question": "这份录音没有字幕文件，也没有可用的本地转写引擎。"
                        "请提供 SRT/VTT，或安装 faster-whisper 后重跑 ingest。",
            "answer_format": {"srt": "str  # 标准 SRT 内容"},
        }
        (gaps / f"{payload['id']}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
