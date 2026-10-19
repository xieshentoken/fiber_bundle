"""Synthetic HTTPS transport in an actual Hermes subprocess; no external requests."""

import io
import json
import runpy
import socket
import sys
import urllib.request

raw = sys.stdin.buffer.read()
payload = json.loads(raw)
sys.stdin = io.TextIOWrapper(io.BytesIO(raw))
original_dns = socket.getaddrinfo


def dns(host, port, *args, **kwargs):
    if host == "opencode.ai":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    return original_dns(host, port, *args, **kwargs)


class Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, _):
        return json.dumps(
            {
                "id": "synthetic-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "synthetic-cloud",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Synthetic target is 37 C."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            }
        ).encode()


class Opener:
    def open(self, request, timeout):
        assert request.full_url == "https://opencode.ai/zen/go/v1/chat/completions"
        assert request.get_header("User-agent") == "fiber-bundle-hermes/0.5.0"
        assert request.get_header("X-opencode-session") == payload["session_id"]
        assert request.get_header("Authorization") == "Bearer synthetic-cloud-key-0000000000000000"
        data = json.loads(request.data)
        assert data["stream"] is False and not data.get("tools")
        assert data["response_format"] == {"type": "json_object"}
        assert data["max_tokens"] == 1024 and timeout <= 30
        return Response()


socket.getaddrinfo = dns
urllib.request.build_opener = lambda *_: Opener()
runpy.run_path(sys.argv[1], run_name="__main__")
