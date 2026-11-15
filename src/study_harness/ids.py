"""Identifier rules.

Block ids are derived from position (see docs/ingest.md §3), never from a global
counter, so that editing one slide does not shift every later id.
"""

from __future__ import annotations

import hashlib
import re

_LECTURE_RE = re.compile(r"第\s*(\d+)\s*[章讲]|lecture[-_ ]?(\d+)", re.IGNORECASE)


def lecture_from_name(name: str) -> int | None:
    """Extract a lecture/chapter number from a filename, if present."""
    m = _LECTURE_RE.search(name)
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def src_key(filename: str, adapter: str) -> str:
    """Short, stable key for a source file.

    Uses the lecture number when the name carries one, so that block ids stay
    readable (`s06-012-3`); falls back to a content-independent hash otherwise.
    """
    n = lecture_from_name(filename)
    if n is not None:
        prefix = {"pptx": "s", "media": "rec", "pdf": "doc", "docx": "doc", "text": "txt"}.get(
            adapter, adapter
        )
        return f"{prefix}{n:02d}"
    h = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:6]
    return f"{adapter}-{h}"


def ascii_slug(text: str, max_len: int = 40) -> str:
    """Deterministic ASCII-only slug; Chinese text falls back to a hash.

    Deterministic extraction cannot transliterate Chinese, so draft units get an
    `auto-` id and are flagged by the `auto_unit_id` gate (see docs/unit-schema.md
    §3.1 and the note in docs/architecture.md §19).
    """
    lowered = text.strip().lower()
    ascii_only = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if len(ascii_only) >= 3:
        slug = ascii_only[:max_len].strip("-")
        # ids must start with a letter; a heading like "6.1 ..." yields "6-1"
        return slug if slug[0].isalpha() else f"sec-{slug}"
    h = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:8]
    return f"auto-{h}"


def normalize_id(raw: str) -> str:
    """Normalize an id to the allowed charset."""
    s = re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()).strip("-")
    if not s or not s[0].isalpha():
        s = "u-" + s
    return s
