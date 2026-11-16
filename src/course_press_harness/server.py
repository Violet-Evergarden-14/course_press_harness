"""服务模式：网页前端 + 对话 + 自动出成品。

只依赖标准库。启动：

    export ANTHROPIC_API_KEY=...
    study serve --workspace my-course

打开 http://127.0.0.1:8765 ：
左侧选素材、选输出格式与文风，右侧看进度、下载成品，底部可以对话提要求。

设计边界：`study` 命令行仍然不调用模型；本模块是**产品层**，它调用 provider 填缺口。
两条路径写同一套文件，因此服务模式产出的书与手工填缺口完全等价。
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from . import agent, provider

WEB_DIR = Path(__file__).resolve().parent / "web"

STATE: dict[str, Any] = {
    "workspace": None,
    "history": [],
    "running": False,
    "logs": [],
}
SUBSCRIBERS: list[queue.Queue[str]] = []
LOCK = threading.Lock()


def publish(line: str) -> None:
    with LOCK:
        STATE["logs"].append(line)
        STATE["logs"] = STATE["logs"][-500:]
        for q in list(SUBSCRIBERS):
            q.put(line)


def _json(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _text(handler: BaseHTTPRequestHandler, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8") -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", ctype)
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


BOUNDARY_RE = re.compile(r"boundary=(?:\"([^\"]+)\"|([^;]+))")


def parse_multipart(body: bytes, content_type: str) -> list[tuple[str, bytes]]:
    """返回 [(filename, data)]。标准库在 3.13 移除了 cgi，这里手写一个够用的解析。"""
    match = BOUNDARY_RE.search(content_type or "")
    if not match:
        return []
    boundary = (match.group(1) or match.group(2) or "").encode()
    if not boundary:
        return []

    files: list[tuple[str, bytes]] = []
    for chunk in body.split(b"--" + boundary):
        if not chunk.strip() or chunk.strip() == b"--":
            continue
        head, _, data = chunk.partition(b"\r\n\r\n")
        if not _:
            continue
        header_text = head.decode("utf-8", "replace")
        name_match = re.search(r'filename="([^"]*)"', header_text)
        if not name_match or not name_match.group(1):
            continue
        files.append((name_match.group(1), data[:-2] if data.endswith(b"\r\n") else data))
    return files


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # 静音默认访问日志
        pass

    # -- GET -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = unquote(self.path.split("?", 1)[0])

        if path in ("/", "/index.html"):
            return self._static("index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            return self._static(path[8:])
        if path == "/api/state":
            ws = STATE["workspace"]
            payload: dict[str, Any] = {
                "workspace": str(ws) if ws else None,
                "running": STATE["running"],
                "provider": {"name": cfg_name(), "missing_key": _missing_key()},
            }
            if ws:
                payload["state"] = agent.summarize(ws)
            payload["settings"] = public_settings()
            return _json(self, payload)
        if path == "/api/events":
            return self._events()
        if path.startswith("/files/"):
            return self._file(path[7:])
        return _text(self, "not found", 404)

    def _static(self, name: str, ctype: str | None = None) -> None:
        target = (WEB_DIR / name).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.exists():
            return _text(self, "not found", 404)
        ctype = ctype or {
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(target.suffix, "application/octet-stream")
        _text(self, target.read_text(encoding="utf-8"), 200, ctype)

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "keep-alive")
        self.end_headers()

        q: queue.Queue[str] = queue.Queue()
        with LOCK:
            SUBSCRIBERS.append(q)
            backlog = STATE["logs"][-100:]
        try:
            for line in backlog:
                self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    line = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with LOCK:
                if q in SUBSCRIBERS:
                    SUBSCRIBERS.remove(q)

    def _file(self, name: str) -> None:
        ws: Path | None = STATE["workspace"]
        if ws is None:
            return _text(self, "no workspace", 400)
        target = (ws / ".study" / "out" / Path(name).name)
        if not target.exists():
            return _text(self, "not found", 404)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("content-type", "application/octet-stream")
        self.send_header("content-disposition", f'attachment; filename="{target.name}"')
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- POST ------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""

        if path == "/api/init":
            return self._init(body)
        if path == "/api/upload":
            return self._upload(body)
        if path == "/api/run":
            return self._run(body)
        if path == "/api/chat":
            return self._chat(body)
        if path == "/api/settings":
            return _settings(self, body)
        return _json(self, {"error": "not found"}, 404)

    def _init(self, body: bytes) -> None:
        data = json.loads(body or b"{}")
        ws = Path(data.get("workspace") or "workspace").resolve()
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "inputs").mkdir(exist_ok=True)
        if not (ws / "study.config.yaml").exists():
            from .cli import main as cli_main

            with _silent():
                cli_main(["--workspace", str(ws), "init", "--course", data.get("course") or "示例课程"])
        STATE["workspace"] = ws
        publish(f"工作区：{ws}")
        return _json(self, {"workspace": str(ws)})

    def _upload(self, body: bytes) -> None:
        ws: Path | None = STATE["workspace"]
        if ws is None:
            return _json(self, {"error": "先创建工作区"}, 400)
        files = parse_multipart(body, self.headers.get("content-type", ""))
        if not files:
            return _json(self, {"error": "没有收到文件"}, 400)

        saved: list[str] = []
        for name, data in files:
            safe = Path(name).name
            (ws / "inputs" / safe).write_bytes(data)
            saved.append(safe)
            publish(f"已上传 {safe}（{len(data) // 1024} KB）")
        _register_lectures(ws, saved)
        return _json(self, {"saved": saved})

    def _run(self, body: bytes) -> None:
        ws: Path | None = STATE["workspace"]
        if ws is None:
            return _json(self, {"error": "先创建工作区"}, 400)
        if STATE["running"]:
            return _json(self, {"error": "已有任务在跑"}, 409)

        data = json.loads(body or b"{}")
        opts = agent.RunOptions.from_dict(data)
        cfg = provider_cfg()

        def worker() -> None:
            STATE["running"] = True
            publish("开始运行")
            try:
                result = agent.auto_run(ws, opts, cfg, log=publish)
                publish("完成" if result.get("ok") else f"中止：{result}")
            except Exception as exc:  # noqa: BLE001
                publish(f"出错：{exc}")
            finally:
                STATE["running"] = False

        threading.Thread(target=worker, daemon=True).start()
        return _json(self, {"started": True}, 202)

    def _chat(self, body: bytes) -> None:
        ws: Path | None = STATE["workspace"]
        if ws is None:
            return _json(self, {"error": "先创建工作区"}, 400)
        data = json.loads(body or b"{}")
        message = str(data.get("message") or "").strip()
        if not message:
            return _json(self, {"error": "消息为空"}, 400)

        try:
            result = agent.chat_turn(ws, message, STATE["history"], provider_cfg(), log=publish)
        except Exception as exc:  # noqa: BLE001
            return _json(self, {"reply": f"出错：{exc}"}, 500)

        STATE["history"].append({"role": "user", "content": message})
        STATE["history"].append({"role": "assistant", "content": result.get("reply", "")})
        result["state"] = agent.summarize(ws)
        return _json(self, result)


def _silent():
    """吞掉命令行实现在初始化时打印到 stdout 的内容。"""
    import contextlib
    import io

    buf = io.StringIO()
    return contextlib.redirect_stdout(buf)


def _register_lectures(ws: Path, names: list[str]) -> None:
    """把新上传的文件登记成讲次，省掉手改配置这一步。"""
    import yaml

    cfg_path = ws / "study.config.yaml"
    if not cfg_path.exists():
        return
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    course = data.setdefault("course", {})
    lectures = course.setdefault("lectures", [])

    for name in names:
        rel = f"inputs/{name}"
        if any(rel in (lec.get("sources") or []) for lec in lectures):
            continue
        from .ids import lecture_from_name

        no = lecture_from_name(name) or (len(lectures) + 1)
        lectures.append({"number": no, "title": Path(name).stem, "sources": [rel]})

    lectures.sort(key=lambda lec: lec.get("number") or 0)
    cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    publish(f"已登记 {len(lectures)} 个讲次")


def public_settings() -> dict[str, Any]:
    cfg = provider_cfg()
    return {
        "provider": cfg.name,
        "model": cfg.model or "",
        "base_url": cfg.base_url or "",
        "has_key": bool(cfg.api_key),
        "key_hint": ("已保存" if cfg.api_key else ""),
    }


def _settings(handler: BaseHTTPRequestHandler, body: bytes) -> None:
    data = json.loads(body or b"{}")
    action = data.get("action") or "save"

    if action == "models":
        try:
            return _json(handler, {"ok": True, "models": provider.list_models(provider_cfg())})
        except provider.ProviderError as exc:
            return _json(handler, {"ok": False, "error": str(exc)}, 400)

    if action == "test":
        try:
            reply = provider.chat(provider_cfg(), system="只回答 ok",
                                  messages=[{"role": "user", "content": "ping"}])
            return _json(handler, {"ok": True, "reply": reply[:40]})
        except provider.ProviderError as exc:
            return _json(handler, {"ok": False, "error": str(exc)}, 400)

    cfg = provider.ProviderConfig(
        name=data.get("provider") or "anthropic",
        model=(data.get("model") or "").strip() or None,
        base_url=(data.get("base_url") or "").strip() or None,
        api_key=(data.get("api_key") or "").strip() or None,
    )
    if not cfg.api_key:                      # 没填新 key 时沿用环境变量
        cfg.api_key = provider.ProviderConfig.from_env(cfg.name).api_key

    STATE["provider"] = cfg
    publish(f"模型设置已更新：{cfg.name}/{cfg.resolved_model}"
            + ("" if cfg.api_key else "（尚未填 API key）"))

    if data.get("remember"):
        _save_provider(cfg, STATE.get("workspace"))

    return _json(handler, {"settings": public_settings()})


def _provider_path(ws: Path | None) -> Path:
    base = ws if ws else Path.cwd()
    return base / ".study" / "provider.json"


def _save_provider(cfg: provider.ProviderConfig, ws: Path | None) -> None:
    """把设置写进工作区 .study/（已被 .gitignore 排除），重启后自动读回。"""
    path = _provider_path(ws)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "provider": cfg.name, "model": cfg.model,
            "base_url": cfg.base_url, "api_key": cfg.api_key,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        publish(f"设置已保存到 {path}")
    except Exception as exc:  # noqa: BLE001
        publish(f"保存设置失败：{exc}")


def _load_provider(ws: Path | None) -> None:
    path = _provider_path(ws)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    STATE["provider"] = provider.ProviderConfig(
        name=data.get("provider") or "anthropic",
        model=data.get("model"), base_url=data.get("base_url"),
        api_key=data.get("api_key"),
    )
    publish(f"已从 {path.name} 读回模型设置")


def provider_cfg() -> provider.ProviderConfig:
    cfg = STATE.get("provider")
    if cfg is None:
        cfg = provider.ProviderConfig.from_env()
        STATE["provider"] = cfg
    return cfg


def cfg_name() -> str:
    return provider_cfg().name


def _missing_key() -> str | None:
    return provider_cfg().missing_key()


def serve(workspace: Path | None, host: str = "127.0.0.1", port: int = 8765) -> None:
    if workspace is not None:
        workspace = workspace.resolve()
        if not (workspace / "study.config.yaml").exists():
            from .cli import main as cli_main

            with _silent():
                cli_main(["--workspace", str(workspace), "init"])
        STATE["workspace"] = workspace

    _load_provider(STATE.get("workspace"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"course_press_harness 服务已启动：http://{host}:{port}")
    print(f"工作区：{STATE['workspace'] or '（尚未创建，可在页面里建）'}")
    missing = _missing_key()
    print(f"模型：{cfg_name()}（{ '缺少 ' + missing if missing else '已配置' }）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
