"""Opt-in isolated GUI acceptance server; all content and inference are synthetic.

Create a new --fixture directory. --read-only is for G1 reference inspection.
The normal mode uses the production API, installed Hermes, and a loopback provider.
It never loads project state or a real model credential.
"""

import argparse
import json
import sys
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn
from fastapi import Response
from starlette.responses import JSONResponse

from fiber_bundle import auth, database, query_policy_store
from fiber_bundle.agent_connections import configuration
from fiber_bundle.agent_connections.contracts import ConnectionSpec
from fiber_bundle.api import create_app
from fiber_bundle.config import Config
from fiber_bundle.execution_config import ExecutionConfig
from fiber_bundle.http_guard import Settings

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]


def prepare(fixture: Path, baseline: Path, provider_port: int, read_only: bool) -> Config:
    fixture.mkdir(mode=0o700)
    config = Config(
        fixture,
        ".state",
        (("personal", "test vault/temp_vault1"), ("supplier", "test vault/temp_vault2")),
    )
    database.initialize(config)
    auth.set_password(config, "synthetic-gui-acceptance")
    manifest = json.loads((baseline / "fixture-manifest.json").read_text())
    for name in manifest["corpus"]:
        dest = fixture / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((baseline / "fixtures/corpus" / name).read_bytes())
    cloud = ExecutionConfig(
        image="sha256:" + "a" * 64,
        endpoint="https://opencode.ai/zen/go/v1/chat/completions",
        model="glm-5.3-flash",
        secret_file=str(fixture / "NO_REAL_CREDENTIAL_CREATED.key"),
        price_revision="synthetic-gui-no-cloud-inference",
        input_nusd=150,
        output_nusd=500,
        max_input_tokens=16384,
        max_output_tokens=2048,
        max_request_bytes=65536,
        task_limit_nusd=4000000,
        daily_limit_nusd=4000000,
        max_calls=1,
        daily_calls=1,
        max_attempts=1,
        timeout_seconds=30,
        timezone="UTC",
    )
    local = (
        None
        if read_only
        else {
            "image": "sha256:" + "a" * 64,
            "endpoint": f"http://127.0.0.1:{provider_port}/v1/chat/completions",
            "model": "synthetic-gui-provider",
            "secret_file": None,
            "max_input_tokens": 8192,
            "max_output_tokens": 1024,
            "max_request_bytes": 65536,
            "max_calls": 1,
            "daily_calls": 30,
            "timeout_seconds": 30,
        }
    )
    query_policy_store.save(
        config,
        {
            "schema": "query-routing-preparation/v1",
            "enabled": False,
            "local_query": local,
            "cloud_answer": asdict(cloud),
            "approved_backups": [],
        },
        None,
    )
    db = database.connect(config.state_root)
    vaults = {r["name"]: r["id"] for r in db.execute("SELECT id,name FROM vaults")}
    for index, name in enumerate(
        [
            "云端 Hermes · 暂停",
            "仅据证据 · 待应用",
            "双库范围 · 暂停",
            "通用 MCP · 待配置",
            "长名称测试：中文知识库与 AB-123 项目 · Long connection name",
            "备用连接 · 未授权",
        ]
    ):
        kind = "mcp" if index == 3 else "hermes"
        spec = ConnectionSpec(
            name=name,
            agent_type=kind,
            vault_ids=list(vaults.values()) if index == 2 else [vaults["personal"]],
            answer_policy="evidence_only" if index == 1 else "augmented",
            model_configuration_id=None if kind == "mcp" else "opencodego-primary",
        )
        configuration.create(config, db, spec, "gui-fixture-" + str(index))
    db.close()
    (fixture / "config.json").write_text(json.dumps(config.storage_configuration()) + "\n")
    return config


def synthetic_provider(port: int, evidence: Path) -> ThreadingHTTPServer:
    lock = threading.Lock()
    calls: list[dict[str, object]] = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > 65536 or self.path != "/v1/chat/completions":
                self.send_response(404)
                self.end_headers()
                return
            request = json.loads(self.rfile.read(length))
            text = json.dumps(request, ensure_ascii=False)
            delayed, failed = "G2延迟" in text, "G2失败" in text
            with lock:
                calls.append({"sequence": len(calls) + 1, "delayed": delayed, "failed": failed})
                evidence.write_text(
                    json.dumps({"synthetic": True, "real_model": False, "calls": calls}, indent=2)
                    + "\n"
                )
            if delayed:
                time.sleep(8)
            if failed:
                self.send_response(500)
                self.end_headers()
                return
            answer = {
                "schema_version": "fiber-agent-answer/v1",
                "claims": [
                    {"text": "在 2 L/min 条件下记录的设定温度为 37.4 °C。", "citations": ["[1]"]}
                ],
                "model_supplement": "此处是合成提供方的一般知识补充，不作为知识库证据。",
            }
            value = {
                "id": "synthetic-gui",
                "object": "chat.completion",
                "created": 0,
                "model": "synthetic-gui-provider",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(answer, ensure_ascii=False),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
            }
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ReadOnly:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope["method"] not in ("GET", "HEAD", "OPTIONS")
            and scope["path"] not in ("/admin/login", "/admin/logout")
        ):
            await JSONResponse({"error": "gui_reference_read_only"}, status_code=403)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18787)
    parser.add_argument("--provider-port", type=int, default=18788)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--core-workflows", action="store_true")
    args = parser.parse_args()
    config = prepare(args.fixture, args.baseline, args.provider_port, args.read_only)
    if args.core_workflows:
        from gui_acceptance_fixtures import prepare_core

        prepare_core(config)
    app = create_app(config, Settings(port=args.port))
    allowed = {
        "offline.html",
        "offline.css",
        "offline.js",
        "tokens.css",
        "fonts.css",
        "dm-sans.ttf",
        "noto-sans-sc.ttf",
        "logo-original.png",
    }

    @app.get("/__fixture__/{filename}")
    def desktop_reference(filename: str):
        if filename not in allowed:
            return Response(status_code=404)
        media = (
            "text/html"
            if filename.endswith(".html")
            else "text/css"
            if filename.endswith(".css")
            else "text/javascript"
            if filename.endswith(".js")
            else "image/png"
            if filename.endswith(".png")
            else "font/ttf"
        )
        return Response((PROJECT / "desktop" / filename).read_bytes(), media_type=media)

    provider = (
        None
        if args.read_only
        else synthetic_provider(args.provider_port, args.fixture / "provider-calls.json")
    )
    print(
        json.dumps(
            {
                "fixture": str(args.fixture),
                "port": args.port,
                "read_only": args.read_only,
                "real_model": False,
            }
        ),
        flush=True,
    )
    try:
        uvicorn.run(
            ReadOnly(app) if args.read_only else app,
            host="127.0.0.1",
            port=args.port,
            log_level="error",
            access_log=False,
        )
    finally:
        if provider:
            provider.shutdown()
            provider.server_close()


if __name__ == "__main__":
    main()
