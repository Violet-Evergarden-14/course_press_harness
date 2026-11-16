"""工具注册表：模型能调用的全部动作。

每个工具都包在已有的确定性实现外面（命令行 / agent 函数），因此模型编排时用的能力
与手工操作完全一致，没有第二套逻辑。

设计约定：
* 参数做校验，路径不出工作区；
* 返回值统一为 {"ok": bool, "output": str, "data": {...}}，output 会被喂回模型；
* 工具不做"聪明"判断——判断留给模型，工具只忠实执行并报告结果。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import agent, provider


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    fn: Callable[..., dict[str, Any]]
    danger: bool = False          # 会产生模型调用 / 花钱

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "args": self.schema}


@dataclass
class ToolContext:
    workspace: Path
    cfg: provider.ProviderConfig
    log: Callable[[str], None] = lambda m: None
    budget: dict[str, int] = field(default_factory=lambda: {"gaps": 60, "calls": 0})

    def spend(self, n: int) -> None:
        self.budget["calls"] += 1
        self.budget["gaps"] = max(0, self.budget["gaps"] - n)


def _result(ok: bool, output: str, **data: Any) -> dict[str, Any]:
    return {"ok": ok, "output": output, "data": data}


# --------------------------------------------------------------------------
# 只读工具
# --------------------------------------------------------------------------

def _status(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    state = agent.summarize(ctx.workspace)
    return _result(True, json.dumps(state, ensure_ascii=False, indent=2), state=state)


def _list_units(ctx: ToolContext, limit: int = 40, **_: Any) -> dict[str, Any]:
    units_dir = ctx.workspace / ".study" / "knowledge" / "units"
    rows = []
    for path in sorted(units_dir.glob("*.md"))[: int(limit)]:
        text = path.read_text(encoding="utf-8")
        head = text.split("---", 2)[1] if text.startswith("---") else ""
        title = ""
        for line in head.splitlines():
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip()
        rows.append({"id": path.stem, "title": title,
                     "draft": "draft: true" in head,
                     "chars": len(text)})
    return _result(True, "\n".join(f"{r['id']:28} {'草稿' if r['draft'] else '已合成'} "
                                   f"{r['chars']:>5}字  {r['title'][:30]}" for r in rows),
                   units=rows)


def _inspect_unit(ctx: ToolContext, unit_id: str = "", **_: Any) -> dict[str, Any]:
    path = ctx.workspace / ".study" / "knowledge" / "units" / f"{unit_id}.md"
    if not path.exists():
        return _result(False, f"没有知识点 {unit_id}")
    text = path.read_text(encoding="utf-8")
    return _result(True, text[:4000], unit=unit_id)


def _gate_report(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    path = ctx.workspace / ".study" / "review" / "findings.jsonl"
    if not path.exists():
        return _result(True, "还没有门禁结果，先跑 build。")
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_gate: dict[str, int] = {}
    for item in items:
        by_gate[item.get("gate", "?")] = by_gate.get(item.get("gate", "?"), 0) + 1
    lines = [f"{k}: {v}" for k, v in sorted(by_gate.items(), key=lambda kv: -kv[1])]
    detail = [f"[{i.get('level')}] {i.get('gate')} {i.get('unit') or ''} — {i.get('message','')[:100]}"
              for i in items[:25]]
    return _result(True, "\n".join(lines) + "\n\n" + "\n".join(detail), findings=len(items))


# --------------------------------------------------------------------------
# 执行工具
# --------------------------------------------------------------------------

def _ingest(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    code, out = agent._run_cli(ctx.workspace, "ingest", log=ctx.log)
    return _result(code == 0, out[-2000:])


def _build(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    code, out = agent._run_cli(ctx.workspace, "build", log=ctx.log)
    return _result(code in (0, 1, 5), out[-3000:], code=code)


def _prepare(ctx: ToolContext, limit: int = 20, **_: Any) -> dict[str, Any]:
    code, out = agent._run_cli(ctx.workspace, "compose", "--limit", str(int(limit)), log=ctx.log)
    return _result(code == 0, out[-1500:])


def _fill(ctx: ToolContext, limit: int = 10, kind: str = "compose_unit", **_: Any) -> dict[str, Any]:
    allowed = min(int(limit), ctx.budget["gaps"])
    if allowed <= 0:
        return _result(False, "本轮缺口预算已用尽，先停下来汇报。")
    stats = agent.fill_gaps(ctx.workspace, ctx.cfg, kinds=(kind,), limit=allowed, log=ctx.log)
    ctx.spend(stats["filled"])
    return _result(stats["filled"] > 0 or stats["failed"] == 0,
                   f"填写 {stats['filled']}，失败 {stats['failed']}（剩余预算 {ctx.budget['gaps']}）",
                   stats=stats)


def _apply(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    code, out = agent._run_cli(ctx.workspace, "compose", "--apply", log=ctx.log)
    return _result(code == 0, out[-1500:])


def _rewrite(ctx: ToolContext, unit_id: str = "", instruction: str = "", **_: Any) -> dict[str, Any]:
    """按反馈重写某个知识点：删掉旧答案，带反馈重新生成，再应用。"""
    if not unit_id:
        return _result(False, "需要 unit_id")
    answers = ctx.workspace / ".study" / "gaps" / "answers" / f"compose-{unit_id}.json"
    if answers.exists():
        answers.unlink()
    _run_cli_compose(ctx, unit_id)
    gap = ctx.workspace / ".study" / "gaps" / f"compose-{unit_id}.json"
    if not gap.exists():
        return _result(False, f"{unit_id} 没有写作任务（可能已不是草稿）")

    payload = json.loads(gap.read_text(encoding="utf-8"))
    payload["user_feedback"] = instruction
    try:
        raw = provider.chat(ctx.cfg, system=_gap_system_with(instruction), json_only=True,
                            messages=[{"role": "user", "content":
                                       "按 feedback 重写，只输出 JSON：\n\n"
                                       + json.dumps({k: v for k, v in payload.items()}, ensure_ascii=False, indent=2)}])
        answer = provider.parse_json(raw)
    except provider.ProviderError as exc:
        return _result(False, f"重写失败：{exc}")
    answer.setdefault("by", f"{ctx.cfg.name}/{ctx.cfg.resolved_model}")
    answers.parent.mkdir(parents=True, exist_ok=True)
    answers.write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.spend(1)
    code, out = agent._run_cli(ctx.workspace, "compose", "--apply", log=ctx.log)
    return _result(True, f"已按反馈重写 {unit_id}。{out[-400:]}")


def _gap_system_with(instruction: str) -> str:
    return (agent.GAP_SYSTEM + "\n用户对上一版的具体意见（必须照做）：" + (instruction or "更详细，成段论述"))


def _run_cli_compose(ctx: ToolContext, unit_id: str) -> None:
    agent._run_cli(ctx.workspace, "compose", "--only", unit_id, "--limit", "1", log=ctx.log)


def _render(ctx: ToolContext, formats: Any = None, profile: str = "learn", **_: Any) -> dict[str, Any]:
    fmts = formats or ["pdf"]
    if isinstance(fmts, str):
        fmts = [f.strip() for f in fmts.split(",") if f.strip()]
    produced: list[str] = []
    for fmt in fmts:
        code, out = agent._run_cli(ctx.workspace, "render", "--format", str(fmt),
                                   "--profile", profile, log=ctx.log)
        if code != 0:
            return _result(False, f"{fmt} 渲染失败：{out[-800:]}")
        produced.append(str(fmt))
    files = sorted(p.name for p in (ctx.workspace / ".study" / "out").glob("book.*") if p.is_file())
    return _result(True, "已产出：" + "、".join(files), formats=produced, files=files)


def _finish(ctx: ToolContext, summary: str = "", **_: Any) -> dict[str, Any]:
    return _result(True, summary or "完成")


REGISTRY: dict[str, Tool] = {
    t.name: t for t in [
        Tool("status", "查看工作区状态：素材、知识点、待填缺口、已产出文件", {}, _status),
        Tool("list_units", "列出知识点及其状态（草稿/已合成）", {"limit": "int"}, _list_units),
        Tool("inspect_unit", "读取某个知识点的完整正文，用于判断质量", {"unit_id": "str"}, _inspect_unit),
        Tool("gate_report", "查看最近一次构建的门禁结果与主要问题", {}, _gate_report),
        Tool("ingest", "重新摄取 inputs/ 下的素材", {}, _ingest),
        Tool("build", "构建：摄取→切分→编排→渲染→门禁（不含模型）", {}, _build),
        Tool("prepare", "为草稿知识点生成写作任务（缺口文件）", {"limit": "int"}, _prepare),
        Tool("fill", "调用模型填写写作任务", {"limit": "int", "kind": "str"}, _fill, danger=True),
        Tool("apply", "把已填写的答案并入知识点", {}, _apply),
        Tool("rewrite", "按具体意见重写某个知识点", {"unit_id": "str", "instruction": "str"}, _rewrite,
             danger=True),
        Tool("render", "渲染成品", {"formats": "[str] 或 'pdf,docx'", "profile": "learn|review"}, _render),
        Tool("finish", "结束本轮，向用户汇报结果", {"summary": "str"}, _finish),
    ]
}


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    tool = REGISTRY.get(name)
    if tool is None:
        return _result(False, f"没有这个工具：{name}。可用：{', '.join(sorted(REGISTRY))}")
    try:
        return tool.fn(ctx, **(args or {}))
    except TypeError as exc:
        return _result(False, f"参数不对：{exc}")
    except Exception as exc:  # noqa: BLE001
        return _result(False, f"执行出错：{exc}")


def describe() -> str:
    return "\n".join(f"- {t.name}: {t.description}  参数 {json.dumps(t.schema, ensure_ascii=False)}"
                     for t in REGISTRY.values())
