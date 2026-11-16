"""Plain text and Markdown adapter (docs/ingest.md §7)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..ids import src_key
from ..ir import Block
from . import register
from .base import Adapter, IngestResult

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET_RE = re.compile(r"^(\s*)([-*+]|\d+[.、)])\s+(.*)$")
CODE_FENCE_RE = re.compile(r"^```(\w*)\s*$")


@register
class TextAdapter(Adapter):
    name = "text"
    accepts = (".txt", ".md", ".markdown")

    def probe(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return {"present": True, "size": path.stat().st_size, "chars": len(text),
                "lines": text.count("\n") + 1}

    def ingest(self, path: Path, ctx: Any) -> IngestResult:
        res = IngestResult()
        key = src_key(path.name, self.name)
        text = path.read_text(encoding="utf-8", errors="replace")
        is_md = path.suffix.lower() in {".md", ".markdown"}

        n = 0

        def nid() -> str:
            nonlocal n
            n += 1
            return f"{key}-{n:04d}"

        in_code = False
        code_lang = None
        code_lines: list[str] = []
        page = 1  # for txt we use a running paragraph index under loc.para

        for raw in text.splitlines():
            line = raw.rstrip()

            fence = CODE_FENCE_RE.match(line.strip()) if is_md else None
            if fence:
                if in_code:
                    res.blocks.append(Block(id=nid(), src=path.name, type="code",
                                            loc={"para": n}, text="\n".join(code_lines),
                                            lang=code_lang or None))
                    code_lines, code_lang, in_code = [], None, False
                else:
                    in_code, code_lang = True, fence.group(1) or None
                continue
            if in_code:
                code_lines.append(raw)
                continue

            if not line.strip():
                continue

            if is_md:
                m = HEADING_RE.match(line)
                if m:
                    res.blocks.append(Block(id=nid(), src=path.name, type="title",
                                            level=len(m.group(1)), loc={"para": n},
                                            text=m.group(2).strip()))
                    continue

            m = BULLET_RE.match(line)
            if m:
                indent, _, body = m.groups()
                res.blocks.append(Block(id=nid(), src=path.name, type="bullet",
                                        level=len(indent.expandtabs(4)) // 2 + 1,
                                        loc={"para": n}, text=body.strip()))
                continue

            res.blocks.append(Block(id=nid(), src=path.name, type="bullet",
                                    level=1, loc={"para": n}, text=line.strip()))

        if in_code and code_lines:
            res.blocks.append(Block(id=nid(), src=path.name, type="code", loc={"para": n},
                                    text="\n".join(code_lines), lang=code_lang or None))

        res.capabilities = self.probe(path)
        return res
