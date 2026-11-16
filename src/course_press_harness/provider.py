"""模型接入层：统一调用 Anthropic / OpenAI 兼容 / 本地端点。

命令行（`study ...`）不经过这里——它保持确定性、可离线、可测试。这一层只服务于
「服务模式」：网页、对话、自动填充缺口。两者共用同一套产物文件，因此服务层跑出来的
结果和手工填缺口完全等价，只是不再需要人去填。

只依赖标准库：HTTP 直接用 urllib，避免为一个功能装 SDK。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-4o",
}

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ProviderError(RuntimeError):
    pass


@dataclass
class ProviderConfig:
    name: str = "anthropic"
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int = 8000
    temperature: float = 0.2
    timeout: int = 300
    retries: int = 2
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, name: str | None = None) -> "ProviderConfig":
        name = name or os.environ.get("CPH_PROVIDER") or "anthropic"
        if name == "anthropic":
            return cls(name="anthropic",
                       api_key=os.environ.get("ANTHROPIC_API_KEY"),
                       model=os.environ.get("CPH_MODEL"))
        return cls(name="openai",
                   api_key=os.environ.get("OPENAI_API_KEY"),
                   model=os.environ.get("CPH_MODEL"),
                   base_url=os.environ.get("CPH_BASE_URL") or OPENAI_URL.rsplit("/chat", 1)[0])

    @property
    def resolved_model(self) -> str:
        return self.model or DEFAULT_MODELS.get(self.name, "")

    @property
    def key_env(self) -> str:
        return "ANTHROPIC_API_KEY" if self.name == "anthropic" else "OPENAI_API_KEY"

    def missing_key(self) -> str | None:
        return None if self.api_key else self.key_env


def _post(url: str, body: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise ProviderError(f"HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"无法连接 {url}：{exc.reason}") from exc


def chat(cfg: ProviderConfig, *, system: str, messages: list[dict[str, str]],
         json_only: bool = False) -> str:
    """一次对话调用。messages 为 [{"role": "user"|"assistant", "content": str}]。"""
    if cfg.missing_key():
        raise ProviderError(f"没有配置 {cfg.missing_key()}：服务模式需要模型凭据，"
                            f"命令行模式不需要")

    last: Exception | None = None
    for attempt in range(cfg.retries + 1):
        try:
            text = _chat_once(cfg, system=system, messages=messages, json_only=json_only)
            return text
        except ProviderError as exc:
            last = exc
            if attempt < cfg.retries:
                time.sleep(1.5 * (attempt + 1))
    raise last or ProviderError("调用失败")


def _chat_once(cfg: ProviderConfig, *, system: str, messages: list[dict[str, str]],
               json_only: bool) -> str:
    if cfg.name == "anthropic":
        body: dict[str, Any] = {
            "model": cfg.resolved_model,
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
            "system": system,
            "messages": messages,
        }
        if json_only:
            body["system"] = system + "\n\n只输出一个 JSON 对象，不要解释文字，不要代码围栏。"
        data = _post(ANTHROPIC_URL, body,
                     {"x-api-key": cfg.api_key or "", "anthropic-version": "2023-06-01"},
                     cfg.timeout)
        return "".join(part.get("text", "") for part in data.get("content", []))

    url = (cfg.base_url or OPENAI_URL.rsplit("/chat", 1)[0]).rstrip("/") + "/chat/completions"
    body = {
        "model": cfg.resolved_model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "messages": [{"role": "system", "content": system + ("\n\n只输出一个 JSON 对象。" if json_only else "")},
                     *messages],
    }
    if json_only:
        body["response_format"] = {"type": "json_object"}
    data = _post(url, body, {"authorization": f"Bearer {cfg.api_key or ''}"}, cfg.timeout)
    return data["choices"][0]["message"]["content"]


def parse_json(raw: str) -> dict[str, Any]:
    """从模型回答里取出 JSON 对象；失败抛 ProviderError。"""
    text = FENCE_RE.sub("", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ProviderError(f"回答里没有 JSON 对象：{text[:200]}")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderError(f"JSON 解析失败：{exc}") from exc
    if not isinstance(obj, dict):
        raise ProviderError("回答不是 JSON 对象")
    return obj
