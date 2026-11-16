"""服务模式的编排层：调模型、填缺口、按用户要求出成品。

命令行保持离线与确定性；这里负责把「需要模型的部分」自动做完，并接受自然语言要求。
两条路径共用同一套文件（缺口、知识点、outline、成品），所以服务层产出的书与人工填缺口的
完全等价，只是不需要人。

成本边界：一次运行最多合成 `max_units` 个知识点，超出部分留到下次，避免一次请求把整学期
的 token 全烧掉。
"""

from __future__ import annotations

import contextlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import provider

Log = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


# --------------------------------------------------------------------------
# 工作区状态
# --------------------------------------------------------------------------

@dataclass
class RunOptions:
    formats: list[str] = field(default_factory=lambda: ["pdf"])
    profile: str = "learn"
    lang: str = "zh"
    style: str = ""
    compose_units: bool = True
    fill_images: bool = False
    max_units: int = 60

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunOptions":
        opts = cls()
        for key in ("profile", "lang", "style"):
            if data.get(key):
                setattr(opts, key, str(data[key]))
        if data.get("formats"):
            opts.formats = [str(f) for f in data["formats"]]
        for key in ("compose_units", "fill_images"):
            if key in data:
                setattr(opts, key, bool(data[key]))
        if data.get("max_units"):
            opts.max_units = int(data["max_units"])
        return opts


def summarize(workspace: Path) -> dict[str, Any]:
    study = workspace / ".study"
    units = list((study / "knowledge" / "units").glob("*.md")) if (study / "knowledge" / "units").exists() else []
    gaps = [p for p in (study / "gaps").glob("*.json")] if (study / "gaps").exists() else []
    answers = list((study / "gaps" / "answers").glob("*.json")) if (study / "gaps" / "answers").exists() else []
    out = sorted(p.name for p in (study / "out").glob("book.*")
                 if p.is_file()) if (study / "out").exists() else []
    inputs = sorted(p.name for p in (workspace / "inputs").glob("*") if p.is_file()) if (workspace / "inputs").exists() else []
    kinds: dict[str, int] = {}
    answered = {a.stem for a in answers}
    for g in gaps:
        if g.stem in answered:
            continue
        try:
            kind = json.loads(g.read_text(encoding="utf-8")).get("kind", "unknown")
        except Exception:
            kind = "unknown"
        kinds[kind] = kinds.get(kind, 0) + 1
    drafted = 0
    for u in units:
        text = u.read_text(encoding="utf-8")
        head = text.split("---", 2)[1] if text.startswith("---") else ""
        if "draft: true" in head:
            drafted += 1
    return {
        "inputs": inputs,
        "units": len(units),
        "drafts": drafted,
        "open_gaps": kinds,
        "answers": len(answers),
        "outputs": out,
    }


# --------------------------------------------------------------------------
# 调用命令行（复用同一套实现，不重复逻辑）
# --------------------------------------------------------------------------

def _run_cli(workspace: Path, *argv: str, log: Log = _noop) -> tuple[int, str]:
    from .cli import main as cli_main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = cli_main(["--workspace", str(workspace), *argv])
    text = buf.getvalue().strip()
    for line in text.splitlines():
        log(line)
    return code, text


# --------------------------------------------------------------------------
# 自动填缺口
# --------------------------------------------------------------------------

GAP_SYSTEM = (
    "你在为一份课程讲义补写内容。严格按 answer_format 输出一个 JSON 对象："
    "不得新增字段，不得省略必填字段，不写解释文字，不用代码围栏。"
    "不确定的内容宁可写 unknown，也不要编造。"
)


def fill_gaps(workspace: Path, cfg: provider.ProviderConfig, *,
              kinds: tuple[str, ...] = ("compose_unit",), limit: int | None = None,
              log: Log = _noop) -> dict[str, int]:
    """把缺口交给模型并写回答案文件。返回 {"filled": n, "failed": n, "skipped": n}。"""
    gaps_dir = workspace / ".study" / "gaps"
    answers_dir = gaps_dir / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    answered = {p.stem for p in answers_dir.glob("*.json")}

    targets: list[dict[str, Any]] = []
    for path in sorted(gaps_dir.glob("*.json")):
        if path.stem in answered:
            continue
        try:
            gap = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if kinds and gap.get("kind") not in kinds:
            continue
        gap["_path"] = str(path)
        targets.append(gap)
        if limit and len(targets) >= limit:
            break

    stats = {"filled": 0, "failed": 0, "skipped": 0}
    for gap in targets:
        payload = {k: v for k, v in gap.items() if not k.startswith("_")}
        try:
            raw = provider.chat(cfg, system=GAP_SYSTEM, json_only=True, messages=[{
                "role": "user",
                "content": "完成任务并输出 JSON：\n\n" + json.dumps(payload, ensure_ascii=False, indent=2),
            }])
            answer = provider.parse_json(raw)
        except provider.ProviderError as exc:
            log(f"× {gap['id']}：{exc}")
            stats["failed"] += 1
            continue

        allowed = set((gap.get("answer_format") or {}).keys())
        extra = sorted(set(answer) - allowed) if allowed else []
        if extra:
            log(f"× {gap['id']}：回答含未声明字段 {extra}，已丢弃")
            stats["failed"] += 1
            continue

        answer.setdefault("by", f"{cfg.name}/{cfg.resolved_model}")
        (answers_dir / f"{gap['id']}.json").write_text(
            json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"√ {gap['id']}（{gap.get('kind')}）")
        stats["filled"] += 1

    stats["skipped"] = max(0, len(list(gaps_dir.glob("*.json"))) - len(targets) - len(answered))
    return stats


# --------------------------------------------------------------------------
# 一次完整运行
# --------------------------------------------------------------------------

def auto_run(workspace: Path, opts: RunOptions, cfg: provider.ProviderConfig,
             log: Log = _noop) -> dict[str, Any]:
    """构建 → 自动合成 → 应用 → 再构建 → 渲染所选格式。"""
    if opts.style:
        _write_style(workspace, opts.style)

    log("① 摄取与切分")
    code, _ = _run_cli(workspace, "build", log=log)
    if code not in (0, 1, 5):
        return {"ok": False, "stage": "build", "code": code}

    if opts.compose_units and cfg.missing_key():
        log(f"② 跳过模型合成：缺少 {cfg.missing_key()}（先用确定性草稿出书）")
    elif opts.compose_units:
        kinds = ("compose_unit", "image_semantics") if opts.fill_images else ("compose_unit",)
        # 先让 compose 生成缺口文件，否则没有东西可填——这一步之前漏了
        log("② 生成写作任务")
        _run_cli(workspace, "compose", "--limit", str(opts.max_units), log=log)

        log(f"③ 调用模型合成（{cfg.name}/{cfg.resolved_model}）")
        stats = fill_gaps(workspace, cfg, kinds=kinds, limit=opts.max_units, log=log)
        log(f"   合成 {stats['filled']}，失败 {stats['failed']}")
        if stats["filled"]:
            _run_cli(workspace, "compose", "--apply", log=log)

    log("④ 重新编排并渲染")
    _run_cli(workspace, "build", log=log)
    formats = ",".join(opts.formats) if opts.formats else "pdf"
    for fmt in formats.split(","):
        _run_cli(workspace, "render", "--format", fmt.strip(), "--profile", opts.profile, log=log)

    state = summarize(workspace)
    return {"ok": True, "state": state,
            "downloads": [f"/files/{name}" for name in state["outputs"]]}


def _write_style(workspace: Path, style: str) -> None:
    """把前端填的写作要求写进配置，和手工编辑 study.config.yaml 等价。"""
    import yaml

    path = workspace / "study.config.yaml"
    if not path.exists():
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("compose", {})["style"] = style
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------------
# 对话
# --------------------------------------------------------------------------

CHAT_SYSTEM = """你在帮助用户用 course_press_harness 把课程素材整理成一本可读的文档。
可以调用的动作（只能选一个）：
- run：开始构建并生成成品。args: {formats: [pdf|md|tex|docx|pptx|all], profile: learn|review, style: 文风要求}
- fill：先补写更多知识点。args: {limit: 数字}
- status：查看当前进度
- answer：不执行动作，只回答。args: {text: 回答内容}

只输出 JSON：{"action": "...", "args": {...}, "reply": "一句话说明你打算做什么"}
需要用户补充信息时（例如没说输出格式、没说文风），用 answer 动作提问，不要擅自假设。"""


def chat_turn(workspace: Path, message: str, history: list[dict[str, str]],
              cfg: provider.ProviderConfig, log: Log = _noop) -> dict[str, Any]:
    state = summarize(workspace)
    context = ("当前工作区状态：\n" + json.dumps(state, ensure_ascii=False, indent=2) +
               "\n\n用户说：" + message)
    try:
        raw = provider.chat(cfg, system=CHAT_SYSTEM, json_only=True,
                            messages=[*history[-8:], {"role": "user", "content": context}])
        plan = provider.parse_json(raw)
    except provider.ProviderError as exc:
        return {"reply": f"模型调用失败：{exc}", "action": None}

    action = plan.get("action")
    args = plan.get("args") or {}
    reply = str(plan.get("reply") or "")
    log(f"模型计划：{action} {args}")

    if action == "run":
        opts = RunOptions.from_dict(args)
        result = auto_run(workspace, opts, cfg, log=log)
        return {"reply": reply or "已开始生成。", "action": action, "result": result}
    if action == "fill":
        stats = fill_gaps(workspace, cfg, limit=int(args.get("limit") or 20), log=log)
        _run_cli(workspace, "compose", "--apply", log=log)
        _run_cli(workspace, "build", log=log)
        return {"reply": reply or f"补写了 {stats['filled']} 个知识点。", "action": action, "stats": stats}
    if action == "status":
        return {"reply": reply or "当前状态如下。", "action": action, "state": state}
    return {"reply": reply or str(args.get("text") or ""), "action": "answer", "state": state}
