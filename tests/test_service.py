"""服务模式：模型接入、对话编排、网页后端（不联网）。"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from course_press_harness import agent, provider, server


# ---------------------------------------------------------------- provider

def test_provider_requires_a_key():
    cfg = provider.ProviderConfig(name="anthropic", api_key=None)
    assert cfg.missing_key() == "ANTHROPIC_API_KEY"
    with pytest.raises(provider.ProviderError):
        provider.chat(cfg, system="s", messages=[{"role": "user", "content": "hi"}])


def test_parse_json_accepts_fenced_output():
    assert provider.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert provider.parse_json('前言 {"a": {"b": 2}} 后记') == {"a": {"b": 2}}


def test_parse_json_rejects_non_json():
    with pytest.raises(provider.ProviderError):
        provider.parse_json("没有 JSON")


# ---------------------------------------------------------------- agent

def test_run_options_from_chat_args():
    opts = agent.RunOptions.from_dict({"formats": ["pdf", "docx"], "profile": "review",
                                       "style": "先讲为什么"})
    assert opts.formats == ["pdf", "docx"]
    assert opts.profile == "review"
    assert opts.style == "先讲为什么"


def test_summarize_counts_gaps_and_outputs(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / ".study" / "knowledge" / "units").mkdir(parents=True)
    (ws / ".study" / "knowledge" / "units" / "u-1.md").write_text(
        "---\nid: u-1\ntitle: 示例\ndraft: true\n---\n\n## 定义\n\n内容\n", encoding="utf-8")
    (ws / ".study" / "gaps").mkdir()
    (ws / ".study" / "gaps" / "compose-u-1.json").write_text(
        json.dumps({"id": "compose-u-1", "kind": "compose_unit"}), encoding="utf-8")
    (ws / ".study" / "out").mkdir()
    (ws / ".study" / "out" / "book.md").write_text("x", encoding="utf-8")
    (ws / ".study" / "out" / "book.assets").mkdir()

    state = agent.summarize(ws)
    assert state["units"] == 1 and state["drafts"] == 1
    assert state["open_gaps"] == {"compose_unit": 1}
    assert state["outputs"] == ["book.md"], "assets 目录不是产物"


def test_auto_run_without_api_key_still_produces_a_book(tmp_path: Path, sample_pptx: Path):
    from course_press_harness.cli import main

    ws = tmp_path / "ws"
    main(["--workspace", str(ws), "init", "--course", "示例课程"])
    (ws / "inputs").mkdir(exist_ok=True)
    (ws / "inputs" / sample_pptx.name).write_bytes(sample_pptx.read_bytes())
    import yaml

    cfg = yaml.safe_load((ws / "study.config.yaml").read_text(encoding="utf-8"))
    cfg["course"]["lectures"] = [{"number": 6, "title": "示例主题",
                                  "sources": [f"inputs/{sample_pptx.name}"]}]
    (ws / "study.config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    logs: list[str] = []
    result = agent.auto_run(ws, agent.RunOptions(formats=["md"]),
                            provider.ProviderConfig(name="anthropic", api_key=None),
                            log=logs.append)
    assert result["ok"] is True
    assert (ws / ".study" / "out" / "book.md").exists()
    assert any("跳过模型合成" in line for line in logs)


# ---------------------------------------------------------------- server

def test_multipart_parser_extracts_files():
    body = (
        b'--BOUND\r\nContent-Disposition: form-data; name="files"; filename="a.pptx"\r\n\r\n'
        b'PK\x03\x04data\r\n--BOUND--\r\n'
    )
    files = server.parse_multipart(body, "multipart/form-data; boundary=BOUND")
    assert files == [("a.pptx", b"PK\x03\x04data")]


def test_server_serves_ui_and_state(tmp_path: Path):
    ws = tmp_path / "ws"
    port = 8799
    threading.Thread(target=server.serve, args=(ws, "127.0.0.1", port), daemon=True).start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            with urllib.request.urlopen(base + "/api/state", timeout=2) as resp:
                payload = json.loads(resp.read())
            break
        except Exception:
            time.sleep(0.2)
    else:  # pragma: no cover
        pytest.fail("服务未在预期时间内就绪")

    assert payload["workspace"] == str(ws)
    assert "provider" in payload

    with urllib.request.urlopen(base + "/", timeout=5) as resp:
        html = resp.read().decode("utf-8")
    assert "course_press_harness" in html and "开始生成" in html
