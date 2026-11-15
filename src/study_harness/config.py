"""Workspace configuration (docs/cli.md §5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_NAME = "study.config.yaml"

DEFAULTS: dict[str, Any] = {
    "schema_version": 1,
    "course": {"name": "untitled", "lang": "zh", "lectures": []},
    "render": {
        "profile": "learn",
        "scope": "semester",
        "source_references": "none",
        "template": "templates/learn/md",
    },
    "alignment": {"tier": "auto", "confirm_threshold": 0.6},
    # how the book should read is the user's call, not a constant in the code
    "compose": {"style": "", "example": ""},
    "review": {"sample_ratio": 0.1, "budget": {"l2_per_lecture": 40, "l3_per_lecture": 15}},
    "glossary": {"path": ".study/glossary.yaml"},
    "ingest": {"ocr": "auto", "docx_comments": False},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    path: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "Config":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(path=path, data=_merge(DEFAULTS, raw))

    @classmethod
    def find(cls, start: Path) -> "Config":
        cur = start.resolve()
        for candidate in [cur, *cur.parents]:
            p = candidate / CONFIG_NAME
            if p.exists():
                return cls.load(p)
        raise FileNotFoundError(f"{CONFIG_NAME} not found from {start}")

    @property
    def workspace(self) -> Path:
        return self.path.parent

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def lectures(self) -> list[dict[str, Any]]:
        return self.get("course", "lectures", default=[]) or []

    @staticmethod
    def lecture_no(lec: dict[str, Any]) -> int | None:
        """Lecture number, tolerating the YAML 1.1 `no` -> boolean False trap.

        YAML 1.1 parses a bare key `no` as the boolean False, so a config written
        with `no: 6` arrives as `{False: 6}`. Both shapes are accepted; new
        configs should use `number:`.
        """
        for key in ("no", "number"):
            v = lec.get(key)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
        for key in (False, "False", "false"):
            v = lec.get(key) if key is not False else lec.get(False)
            if isinstance(v, int) and not isinstance(v, bool):
                return v
        return None

    def lecture_for_source(self, source_path: str) -> dict[str, Any] | None:
        name = Path(source_path).name
        for lec in self.lectures():
            for s in lec.get("sources", []) or []:
                if Path(s).name == name:
                    return lec
        return None

    def config_hash(self) -> str:
        import hashlib
        import json

        payload = json.dumps(self.data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
