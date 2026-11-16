"""工具循环：模型自己选工具、看结果、再决定下一步（用脚本化的假模型测试）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from course_press_harness import loop, provider
from course_press_harness.cli import main


def _ws(tmp_path: Path, pptx: Path) -> Path:
    ws = tmp_path / "ws"
    main(["--workspace", str(ws), "init", "--course", "示例课程"])
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / "inputs" / pptx.name).write_bytes(pptx.read_bytes())
    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"] = [{"number": 6, "title": "示例主题",
                                  "sources": [f"inputs/{pptx.name}"]}]
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ws


class FakeModel:
    """按脚本返回工具调用；填写缺口时返回一段像样的答案。"""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls: list[str] = []

    def __call__(self, cfg, *, system, messages, json_only=False):
        last = messages[-1]["content"]
        if "answer_format" in last:                      # 这是 fill / rewrite 的缺口
            self.calls.append("gap")
            return json.dumps({
                "title": "示例概念",
                "sections": {"概念": ["这是第一段连贯的论述，用来代替罗列。"],
                             "例子": ["给一个能照着算的例子。"]},
                "origin": "synthesized",
            }, ensure_ascii=False)
        self.calls.append("step")
        if not self.script:
            return json.dumps({"tool": "finish", "args": {"summary": "脚本用完了"}})
        return json.dumps(self.script.pop(0), ensure_ascii=False)


@pytest.fixture
def fake_model(monkeypatch):
    def install(script):
        fake = FakeModel(script)
        monkeypatch.setattr(provider, "chat", fake)
        return fake

    return install


def test_loop_runs_tools_in_sequence(tmp_path, sample_pptx, fake_model):
    ws = _ws(tmp_path, sample_pptx)
    fake_model([
        {"thought": "先看状态", "tool": "status", "args": {}},
        {"thought": "构建", "tool": "build", "args": {}},
        {"thought": "生成写作任务", "tool": "prepare", "args": {"limit": 2}},
        {"thought": "调用模型", "tool": "fill", "args": {"limit": 2}},
        {"thought": "并入正文", "tool": "apply", "args": {}},
        {"thought": "自查", "tool": "gate_report", "args": {}},
        {"thought": "渲染", "tool": "render", "args": {"formats": ["md"]}},
        {"tool": "finish", "args": {"summary": "已生成 book.md"}},
    ])

    logs: list[str] = []
    result = loop.run(ws, "生成一份讲义", provider.ProviderConfig(name="openai", api_key="x"),
                      max_steps=12, log=logs.append)

    assert result.final == "已生成 book.md"
    assert "book.md" in result.files
    assert (ws / ".study" / "out" / "book.md").exists()
    assert any(line.startswith("[1] status") for line in logs)
    assert any("√" in line for line in logs)

    # 每次工具调用都要留痕，出问题能回放
    traces = list((ws / ".study" / "logs").glob("agent-*.jsonl"))
    assert traces, "缺少调用日志"
    kinds = [json.loads(line)["kind"] for line in traces[0].read_text(encoding="utf-8").splitlines()]
    assert kinds[0] == "start" and kinds[-1] == "end"
    assert kinds.count("plan") >= 8


def test_loop_stops_on_model_failure(tmp_path, sample_pptx, monkeypatch):
    ws = _ws(tmp_path, sample_pptx)

    def boom(*a, **k):
        raise provider.ProviderError("HTTP 401（API key 无效或已过期）")

    monkeypatch.setattr(provider, "chat", boom)
    result = loop.run(ws, "生成", provider.ProviderConfig(name="openai", api_key="bad"))
    assert result.aborted is True
    assert "401" in result.final


def test_gap_budget_is_enforced(tmp_path, sample_pptx, fake_model):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    main(["--workspace", str(ws), "compose", "--limit", "50"])

    fake_model([
        {"tool": "fill", "args": {"limit": 999}},
        {"tool": "finish", "args": {"summary": "到预算了"}},
    ])
    result = loop.run(ws, "尽量多写", provider.ProviderConfig(name="openai", api_key="x"),
                      gap_budget=3)
    filled = len(list((ws / ".study" / "gaps" / "answers").glob("compose-*.json")))
    assert filled <= 3, f"超出预算是花钱风险：填了 {filled}"
    assert result.final == "到预算了"


def test_rewrite_uses_feedback(tmp_path, sample_pptx, fake_model):
    ws = _ws(tmp_path, sample_pptx)
    main(["--workspace", str(ws), "build"])
    fake_model([
        {"tool": "prepare", "args": {"limit": 1}},
        {"tool": "fill", "args": {"limit": 1}},
        {"tool": "apply", "args": {}},
        {"tool": "list_units", "args": {}},
    ])
    loop.run(ws, "先合成一个", provider.ProviderConfig(name="openai", api_key="x"))

    units = sorted((ws / ".study" / "knowledge" / "units").glob("*.md"))
    target = units[0].stem
    fake_model([
        {"tool": "rewrite", "args": {"unit_id": target, "instruction": "把条目改写成连贯段落"}},
        {"tool": "finish", "args": {"summary": "已重写"}},
    ])
    logs: list[str] = []
    loop.run(ws, "这一讲太碎，重写", provider.ProviderConfig(name="openai", api_key="x"),
             log=logs.append)
    assert any("rewrite" in line for line in logs)
