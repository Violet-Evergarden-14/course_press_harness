"""工具循环：模型自己决定每一步用哪个工具，看到结果再决定下一步。

这不是"问一次模型拿一段文字"，而是：

    用户要求 → 模型选工具 → 执行 → 结果回喂 → 模型再选 → … → finish

三条硬约束，都是为了让"效果必须好"可落地而不是口号：

1. **可观察**：每次工具调用与结果都写进 `.study/logs/agent-*.jsonl`，出问题能回放。
2. **有预算**：工具调用次数与缺口数量都有上限，用完必须汇报，不允许无限烧 token。
3. **要自查**：系统提示要求模型在 render 之前先 build + gate_report，
   有问题就用 inspect_unit 读回来判断，需要时 rewrite——把"审一遍再交付"写进流程。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import provider
from .tools import REGISTRY, ToolContext, describe, run_tool

SYSTEM = """你是 course_press_harness 的执行代理。你的任务是把用户的课程素材整理成一本
可直接阅读的文档，并产出用户要求的格式。

每一步只能输出一个 JSON 对象，表示你要调用哪个工具：

{{"thought": "为什么这一步这么做（一句话）", "tool": "工具名", "args": {{...}}}}

规则（按重要性排序）：
1. 先看状态再动手：不确定工作区里有什么时，先 status。
2. 交付前必须自查：render 之前先 build，再用 gate_report 看问题；
   对关键知识点可以 inspect_unit 读回正文，判断是否成段、是否有编造。
3. 发现某个知识点写得不好，用 rewrite 带具体意见重写（"补一个能手算的例子"
   "把并列条目改写成一段因果叙述"），而不是整篇重来。
4. 不要编造：工具报告失败就如实告诉用户，不要假装完成。
5. 预算有限：一次最多推进一小批（fill 的 limit 建议 10~20），做完汇报进度。
6. 收尾必须调用 finish，summary 里写清楚：做了几讲、产出哪些文件、还差什么。

可用工具：
{tools}
"""


@dataclass
class LoopResult:
    final: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    aborted: bool = False


def _summarise(result: dict[str, Any], limit: int = 1800) -> str:
    text = result.get("output") or ""
    if len(text) > limit:
        text = text[:limit] + f"\n…（截断，共 {len(text)} 字符）"
    return text


def run(workspace: Path, instruction: str, cfg: provider.ProviderConfig, *,
        history: list[dict[str, str]] | None = None, max_steps: int = 16,
        gap_budget: int = 60, log: Callable[[str], None] = lambda m: None) -> LoopResult:
    workspace = workspace.resolve()
    ctx = ToolContext(workspace=workspace, cfg=cfg, log=log,
                      budget={"gaps": gap_budget, "calls": 0})

    log_dir = workspace / ".study" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    trace_path = log_dir / f"agent-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"

    def trace(kind: str, payload: dict[str, Any]) -> None:
        with trace_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, **payload}, ensure_ascii=False) + "\n")

    messages: list[dict[str, str]] = list(history or [])
    messages.append({"role": "user", "content": instruction})
    result = LoopResult(final="")
    trace("start", {"instruction": instruction, "model": cfg.resolved_model})

    for step in range(1, max_steps + 1):
        try:
            raw = provider.chat(cfg, system=SYSTEM.replace("{tools}", describe()),
                                json_only=True, messages=messages)
            plan = provider.parse_json(raw)
        except provider.ProviderError as exc:
            result.final = f"模型调用失败：{exc}"
            result.aborted = True
            trace("error", {"step": step, "error": str(exc)})
            log(result.final)
            break

        tool = str(plan.get("tool") or "").strip()
        args = plan.get("args") or {}
        thought = str(plan.get("thought") or "")
        log(f"[{step}] {tool} {json.dumps(args, ensure_ascii=False)[:120]}"
            + (f"  —— {thought}" if thought else ""))
        trace("plan", {"step": step, "tool": tool, "args": args, "thought": thought})

        if tool in ("finish", "answer"):
            result.final = str((args or {}).get("summary") or (args or {}).get("text") or "")
            trace("finish", {"step": step, "summary": result.final})
            break

        outcome = run_tool(ctx, tool, args)
        trace("result", {"step": step, "tool": tool, "ok": outcome.get("ok"),
                         "output": outcome.get("output", "")[:4000]})
        log(("√ " if outcome.get("ok") else "× ") + _summarise(outcome, 300).replace("\n", " "))

        if tool == "render" and outcome.get("ok"):
            result.files = outcome.get("data", {}).get("files", [])

        messages.append({"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)})
        messages.append({"role": "user", "content":
                         f"工具 {tool} 的结果：\n{_summarise(outcome)}\n\n"
                         f"（已用 {ctx.budget['calls']} 次工具调用；"
                         f"缺口预算剩余 {ctx.budget['gaps']}）下一步？"})
    else:
        result.final = result.final or "达到最大步数，先停在这里。"
        result.aborted = True
        trace("aborted", {"reason": "max_steps"})

    trace("end", {"final": result.final, "files": result.files, "steps": len(result.steps)})
    result.steps = messages
    return result


def tools_for_prompt() -> str:
    return describe()
