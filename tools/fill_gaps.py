#!/usr/bin/env python3
"""Fill study-harness gap files with any model, keeping the CLI model-free.

The harness never calls a model itself: it writes a task file and reads an answer file.
This script is one way to produce those answers — swap the provider, or write your own.

    export ANTHROPIC_API_KEY=...
    python tools/fill_gaps.py --workspace my-course --kind compose_unit --limit 5

    python tools/fill_gaps.py --workspace my-course --provider openai \\
        --base-url https://api.openai.com/v1 --model gpt-4o-mini

    python tools/fill_gaps.py --workspace my-course --dry-run   # 只看会发什么

答案写到 .study/gaps/answers/<id>.json，随后 `study compose --apply` 或 `study review apply`
把它并入正文。脚本只做一件事：把缺口喂给模型、把回答存成文件，不修改任何课程内容。

标准库实现，不引入额外依赖。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = (
    "你在为一份课程讲义补写内容。只输出一个 JSON 对象，不要有任何解释文字、"
    "不要用 Markdown 代码围栏。字段必须符合给定的 answer_format，不得新增字段。"
)


def find_workspace(start: Path) -> Path:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "study.config.yaml").exists():
            return candidate
    raise SystemExit(f"找不到 study.config.yaml（从 {start} 向上查找）")


def load_gaps(workspace: Path, kind: str | None, limit: int | None) -> list[dict[str, Any]]:
    gaps_dir = workspace / ".study" / "gaps"
    answers_dir = gaps_dir / "answers"
    answered = {p.stem for p in answers_dir.glob("*.json")} if answers_dir.exists() else set()

    gaps: list[dict[str, Any]] = []
    for path in sorted(gaps_dir.glob("*.json")):
        if path.stem in answered:
            continue
        try:
            gap = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if kind and gap.get("kind") != kind:
            continue
        gap["_path"] = str(path)
        gaps.append(gap)
        if limit and len(gaps) >= limit:
            break
    return gaps


def build_prompt(gap: dict[str, Any]) -> str:
    payload = {k: v for k, v in gap.items() if not k.startswith("_")}
    return (
        "下面是一个待完成的任务。按 answer_format 输出 JSON。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def call_anthropic(prompt: str, model: str, api_key: str, max_tokens: int) -> str:
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(ANTHROPIC_URL, data=body, method="POST", headers={
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return "".join(block.get("text", "") for block in data.get("content", []))


def call_openai(prompt: str, model: str, api_key: str, base_url: str, max_tokens: int) -> str:
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                 method="POST", headers={
        "content-type": "application/json",
        "authorization": f"Bearer {api_key}",
    })
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_answer(raw: str) -> dict[str, Any]:
    text = FENCE_RE.sub("", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("回答里没有 JSON 对象")
    return json.loads(text[start:end + 1])


def validate(answer: dict[str, Any], gap: dict[str, Any]) -> list[str]:
    allowed = set((gap.get("answer_format") or {}).keys())
    if not allowed:
        return []
    extra = set(answer) - allowed
    return sorted(extra)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="用任意模型填写 study-harness 的缺口文件")
    ap.add_argument("--workspace", default=".", help="工作区目录（含 study.config.yaml）")
    ap.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    ap.add_argument("--model", default=None, help="模型名；默认按 provider 选择")
    ap.add_argument("--base-url", default=OPENAI_URL.rsplit("/chat", 1)[0],
                    help="OpenAI 兼容端点")
    ap.add_argument("--kind", default=None, help="只处理某类缺口，如 compose_unit")
    ap.add_argument("--limit", type=int, default=None, help="最多处理几个")
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--dry-run", action="store_true", help="只打印将要发送的内容")
    args = ap.parse_args(argv)

    workspace = find_workspace(Path(args.workspace))
    gaps = load_gaps(workspace, args.kind, args.limit)
    if not gaps:
        print("没有待填缺口（answers/ 已覆盖，或 --kind 过滤后为空）")
        return 0

    if args.dry_run:
        for gap in gaps:
            print(f"===== {gap['id']} ({gap.get('kind')}) =====")
            print(build_prompt(gap)[:600], "…\n")
        print(f"共 {len(gaps)} 个缺口；去掉 --dry-run 即开始调用模型")
        return 0

    if args.provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        model = args.model or "claude-sonnet-5-5"
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        model = args.model or "gpt-4o-mini"
    if not api_key:
        print(f"缺少 API key：请设置 {'ANTHROPIC_API_KEY' if args.provider == 'anthropic' else 'OPENAI_API_KEY'}",
              file=sys.stderr)
        return 2

    answers_dir = workspace / ".study" / "gaps" / "answers"
    answers_dir.mkdir(parents=True, exist_ok=True)
    ok = failed = 0

    for gap in gaps:
        prompt = build_prompt(gap)
        try:
            if args.provider == "anthropic":
                raw = call_anthropic(prompt, model, api_key, args.max_tokens)
            else:
                raw = call_openai(prompt, model, api_key, args.base_url, args.max_tokens)
            answer = parse_answer(raw)
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            print(f"× {gap['id']}: {exc}", file=sys.stderr)
            failed += 1
            continue

        extra = validate(answer, gap)
        if extra:
            print(f"× {gap['id']}: 回答含未声明字段 {extra}，已丢弃（模型输出不可信）", file=sys.stderr)
            failed += 1
            continue

        answer.setdefault("by", f"{args.provider}/{model}")
        (answers_dir / f"{gap['id']}.json").write_text(
            json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"√ {gap['id']} → {answers_dir / (gap['id'] + '.json')}")
        ok += 1

    print(f"\n完成 {ok}，失败 {failed}。下一步：study compose --apply（或 study review apply）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
