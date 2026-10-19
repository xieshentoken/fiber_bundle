"""Isolated model GUI fixture. Synthetic credentials and loopback inference only."""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn

from fiber_bundle import auth, database
from fiber_bundle.api import create_app
from fiber_bundle.config import Config
from fiber_bundle.http_guard import Settings
from fiber_bundle.model_configuration import keychain


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18851)
    parser.add_argument("--provider-port", type=int, default=18852)
    args = parser.parse_args()
    assert 18000 <= args.port < 19000 and 18000 <= args.provider_port < 19000
    args.root.mkdir(mode=0o700, exist_ok=False)
    config = Config(
        args.root,
        ".state",
        (("personal", "test vault/temp_vault1"), ("supplier", "test vault/temp_vault2")),
    )
    database.initialize(config)
    auth.set_password(config, "synthetic-model-ui")
    values = {}
    keychain.put = lambda account, value: values.__setitem__(account, value)
    keychain.get = lambda account: values[account]

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send({"data": [{"id": "synthetic/model:1"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["model"] == "synthetic/model:1"
            self.send(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                }
            )

        def send(self, body):
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", args.provider_port), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        uvicorn.run(
            create_app(config, Settings(port=args.port)),
            host="127.0.0.1",
            port=args.port,
            log_level="error",
            access_log=False,
        )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
