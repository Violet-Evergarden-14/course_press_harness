"""Layout templates as data (docs/rendering.md §3).

v0.1 implements a *manifest* — a data file that controls heading formats, section
order and folding. Full file-per-layer template files are planned; the manifest
exists so that changing the layout never requires touching code, which is the
property that actually matters for users.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BUILTIN: dict[str, Any] = {
    "version": 1,
    "document": {
        "title": "# {title}",
        "toc_heading": "## 目录",
        "toc_part": "- 第 {label} 讲 {title}",
        "toc_part_other": "- {title}",
        "toc_section": "  - {label}.{index} {title}",
        "toc_unit": "    - {number} {title}",
    },
    "part": {"lecture": "# 第 {label} 讲 {title}", "other": "# {title}"},
    "section": {"heading": "## {label}.{index} {title}"},
    "unit": {
        "heading": "### {number} {title}",
        "section_label": "**{name}**",
        "omit_heading_if_same": True,
        "remedial_label": "> **补充说明**（为便于理解而添加，非课程原有内容）",
    },
    "section_order": {
        "learn": ["定义", "陈述", "说明", "问题", "步骤", "复杂度", "依据", "用途", "算式", "表格",
                  "图示", "图示说明", "图示标注", "例子", "示例", "验证", "易错", "错误说法",
                  "正确说法", "原因", "要点", "备注"],
        "review": ["陈述", "定义", "要点", "易错", "错误说法", "正确说法", "原因", "用途", "依据",
                   "说明", "问题", "步骤", "复杂度", "算式", "表格", "图示", "图示说明", "图示标注",
                   "例子", "示例", "验证", "备注"],
    },
    "collapse": {"learn": [], "review": []},
    "appendix": {
        "focus_heading": "## 附录 A 重点清单",
        "focus_item": "- §{number} {title}",
        "formula_heading": "## 附录 B 公式速查",
        "formula_group": "**§{number} {title}**",
    },
}


@dataclass
class TemplateSet:
    data: dict[str, Any] = field(default_factory=lambda: BUILTIN)
    source: Path | None = None

    @classmethod
    def load(cls, path: Path | None) -> "TemplateSet":
        if path is None or not path.exists():
            return cls()
        if path.is_dir():
            path = path / "manifest.yaml"
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(data=_deep_merge(BUILTIN, raw), source=path)

    @property
    def hash(self) -> str:
        payload = yaml.safe_dump(self.data, sort_keys=True, allow_unicode=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def fmt(self, *keys: str, **values: Any) -> str:
        node: Any = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return ""
            node = node[k]
        try:
            return str(node).format(**values)
        except (KeyError, IndexError):
            return str(node)

    def order(self, profile: str) -> list[str]:
        orders = self.data.get("section_order", {})
        return list(orders.get(profile) or orders.get("learn") or [])

    def collapsed(self, profile: str) -> set[str]:
        c = self.data.get("collapse", {})
        return set(c.get(profile) or [])

    def omit_duplicate_heading(self) -> bool:
        return bool(self.data.get("unit", {}).get("omit_heading_if_same", True))


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
