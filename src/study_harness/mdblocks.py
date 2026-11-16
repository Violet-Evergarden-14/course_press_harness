"""Parse composer markdown into neutral blocks.

The LaTeX and HTML renderers used to each carry their own copy of this parser — box
detection, fences, pipe tables, ordered/unordered runs — and the copies drifted (the
fence-inside-box bug was fixed in one and not the other). Parsing lives here now;
renderers only decide how to serialise a block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union

BOX_OPEN_RE = re.compile(r"^:::(note|warn)\s*(.*)$")
BULLET_RE = re.compile(r"^[-*•]\s+(.*)$")
NUM_ITEM_RE = re.compile(r"^\d+[.、)]\s+(.*)$")
EQUATION_RE = re.compile(r"^\$\$(.*)\$\$$", re.S)
TABLE_SEP_RE = re.compile(r"^[\s|:=-]+$")


@dataclass
class Box:
    kind: str                      # "note" | "warn"
    title: str
    children: list["Block"] = field(default_factory=list)


@dataclass
class Code:
    text: str
    lang: str = ""


@dataclass
class Table:
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Items:
    ordered: bool
    items: list[str] = field(default_factory=list)


@dataclass
class Equation:
    tex: str


@dataclass
class Paragraph:
    text: str


Block = Union[Box, Code, Table, Items, Equation, Paragraph]


def parse_blocks(lines: list[str]) -> list[Block]:
    blocks: list[Block] = []
    i = 0
    n = len(lines)

    def at(k: int, pattern: re.Pattern) -> re.Match | None:
        return pattern.match(lines[k].strip()) if k < n else None

    while i < n:
        raw = lines[i]
        line = raw.strip()
        if not line:
            i += 1
            continue

        opener = BOX_OPEN_RE.match(line)
        if opener:
            kind, title = opener.group(1), opener.group(2).strip()
            inner: list[str] = []
            i += 1
            depth = 0
            while i < n:
                cur = lines[i].strip()
                if cur == ":::":                       # closing marker of this box
                    break
                if BOX_OPEN_RE.match(cur):
                    depth += 1
                inner.append(lines[i])
                i += 1
            i += 1                                     # skip ":::"
            blocks.append(Box(kind, title, parse_blocks(inner)))
            continue

        if line.startswith("```"):
            lang = line[3:].strip()
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])                  # keep indentation
                i += 1
            i += 1
            blocks.append(Code("\n".join(code), lang))
            continue

        if line.startswith("|"):
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append(cells)
                i += 1
            if len(rows) >= 2:
                blocks.append(Table(rows))
            elif rows:
                blocks.append(Paragraph(" ".join(rows[0])))
            continue

        if BULLET_RE.match(line) or NUM_ITEM_RE.match(line):
            ordered = bool(NUM_ITEM_RE.match(line))
            pattern = NUM_ITEM_RE if ordered else BULLET_RE
            items: list[str] = []
            while i < n:
                m = pattern.match(lines[i].strip())
                if not m:
                    break
                items.append(m.group(1).strip())
                i += 1
            blocks.append(Items(ordered, items))
            continue

        m = EQUATION_RE.match(line)
        if m:
            blocks.append(Equation(m.group(1).strip()))
            i += 1
            continue

        blocks.append(Paragraph(line))
        i += 1

    return blocks
