"""Opt-in real Docker/Pi qualification with synthetic replies only; never a provider runner."""

import argparse
import hashlib
import json
import os
import selectors
import subprocess
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import fiber_bundle
from fiber_bundle import document_creation_output as output
from fiber_bundle import document_creation_request as envelope
from fiber_bundle import executor_container, model_gateway
from fiber_bundle.config import InvalidInput
from fiber_bundle.document_creation_contract import CreationPlan
from fiber_bundle.execution_config import ExecutionConfig
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest

BASE_IMAGE = "sha256:f4d9f5f11635ad96fc7cdc9de76cbb210bf98c108cf1c80d8deb81cf1bf52a7f"


def image_info(image):
    result = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, check=True, timeout=15
    )
    return json.loads(result.stdout)[0]


def synthetic_policy(image):
    policy = ExecutionConfig(
        image,
        "https://opencode.ai/zen/go/v1/chat/completions",
        "synthetic-model",
        "/synthetic/not-read",
        "synthetic-known-fees",
        150,
        500,
        131072,
        512,
        131072,
        40000000,
        80000000,
        2,
        2,
        1,
        45,
        "UTC",
    )
    policy.validate()
    return policy


def synthetic_plan(fmt):
    source = {
        "client_id": "synthetic-client",
        "query_task_id": "synthetic-query",
        "binding_sha256": "0" * 64,
        "vault_ids": ["synthetic-vault"],
        "source_version_id": None,
        "sources": [{"citation": "source-1", "snippet": "100 psi synthetic evidence"}],
        "quotations": [{"citation": "source-1", "quote": "100 psi synthetic evidence"}],
    }
    return CreationPlan(
        "synthetic-client", "synthetic-query", canonical(source), ("Prepare a draft",), fmt
    )


def qualify(image, evidence, case, tool_args):
    fmt = "txt" if case == "txt" else "md"
    policy, plan = synthetic_policy(image), synthetic_plan(fmt)
    task = evidence / case
    task.mkdir(mode=0o777)
    task.chmod(0o777)  # Only synthetic inputs; allow the non-root container to create its outputs.
    run_id = str(uuid4())
    fixed = envelope.request(policy, plan)
    context = output.context(plan, run_id, policy.digest, digest(canonical(fixed).encode()))
    init = {
        "type": "init",
        "model": policy.model,
        "format": fmt,
        "context": context,
        "prompt": envelope.PROMPT + "\n" + fixed["messages"][1]["content"],
        "output": policy.max_output_tokens,
        "contextTokens": policy.max_input_tokens,
        "inputPrice": policy.input_nusd / 1000,
        "outputPrice": policy.output_nusd / 1000,
    }
    command = executor_container.command(image, task, run_id, network="none")
    requests, events, buffer = [], [], b""
    if case == "existing":
        (task / "draft.md").write_bytes(b"existing bytes must survive")
    if case == "symlink":
        (task / "draft.md").symlink_to("/tmp/escape.md")
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert process.stdin and process.stdout and process.stderr
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "out")
    selector.register(process.stderr, selectors.EVENT_READ, "err")
    deadline, ended = time.monotonic() + 45, False
    try:
        process.stdin.write((canonical(init) + "\n").encode())
        process.stdin.flush()
        while not ended:
            if time.monotonic() >= deadline:
                raise TimeoutError("synthetic Pi qualification timed out")
            for key, _ in selector.select(0.2):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    if process.poll() is not None and not ended:
                        raise RuntimeError("Pi exited without completion")
                    continue
                if key.data == "err":
                    continue  # Never log arbitrary runtime/model diagnostics.
                buffer += chunk
                if len(buffer) > 512 * 1024:
                    raise InvalidInput("qualification_frame_too_large")
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    frame = json.loads(raw)
                    if frame["type"] == "event":
                        events.append(frame["event"])
                        if frame["event"] == "agent_end":
                            ended = True
                        continue
                    if frame["type"] != "model" or len(requests) >= 2:
                        raise InvalidInput("qualification_protocol_invalid")
                    actual = model_gateway.prepare(policy, frame["body"])
                    tools = actual.get("tools", [])
                    if (
                        len(tools) != 1
                        or tools[0].get("function", {}).get("name") != "fiber_write_draft"
                    ):
                        raise InvalidInput("qualification_tool_scope_invalid")
                    wire = json.dumps(actual, allow_nan=False).encode()
                    if (
                        len(wire) > policy.max_request_bytes
                        or len(wire) + 128 > policy.max_input_tokens
                    ):
                        raise InvalidInput("qualification_actual_request_limit")
                    requests.append(
                        {
                            "sha256": digest(canonical(actual).encode()),
                            "wire_bytes": len(wire),
                            "limits_checked": True,
                        }
                    )
                    calls = (
                        [tool_args]
                        if len(requests) == 1
                        else (
                            [{"draft": "second write must be rejected"}] if case == "repeat" else []
                        )
                    )
                    message = {"role": "assistant", "content": "Synthetic completion"}
                    if calls:
                        message.update(
                            content=None,
                            tool_calls=[
                                {
                                    "id": "synthetic-call-" + str(len(requests)),
                                    "type": "function",
                                    "function": {
                                        "name": "fiber_write_draft",
                                        "arguments": json.dumps(calls[0], ensure_ascii=True),
                                    },
                                }
                            ],
                        )
                    body = {
                        "id": "synthetic",
                        "created": 0,
                        "model": policy.model,
                        "choices": [
                            {
                                "index": 0,
                                "message": message,
                                "finish_reason": "tool_calls" if calls else "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                    }
                    process.stdin.write(
                        (
                            canonical(
                                {"type": "reply", "id": frame["id"], "ok": True, "body": body}
                            )
                            + "\n"
                        ).encode()
                    )
                    process.stdin.flush()
        process.stdin.close()
        assert process.wait(timeout=10) == 0
    finally:
        selector.close()
        if process.poll() is None:
            executor_container.stop(run_id)
            process.kill()
            process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
        if not process.stdin.closed:
            process.stdin.close()
    valid = case in {"md", "txt", "repeat"}
    if valid:
        report = output.inspect(task, plan, context, upload_limit_bytes=1024 * 1024).report()
        assert (
            output.inspect(task, plan, context).content
            == (output.DRAFT_MARKER + tool_args["draft"]).encode()
        )
        assert (task / ("draft." + fmt)).stat().st_mode & 0o777 == 0o600
        assert (task / "output.json").stat().st_mode & 0o777 == 0o600
    else:
        try:
            output.inspect(task, plan, context)
        except InvalidInput:
            report = {"state": "rejected"}
        else:
            raise AssertionError("invalid draft accepted")
        assert not (task / "output.json").exists()
        if case == "existing":
            assert (task / "draft.md").read_bytes() == b"existing bytes must survive"
        if case == "symlink":
            assert (task / "draft.md").is_symlink()
    assert len(requests) == 2
    return {
        "case": case,
        "state": "PASS",
        "output": report,
        "actual_requests": requests,
        "model_frames": len(requests),
        "events": sorted(set(events)),
        "network": "none",
        "real_provider_calls": 0,
        "main_database_used": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    synthetic_policy(args.image)  # Require an immutable SHA; do not execute mutable tags.
    args.evidence.mkdir(parents=True, exist_ok=True)
    base, candidate = image_info(BASE_IMAGE), image_info(args.image)
    assert base["Id"] == BASE_IMAGE and candidate["Id"] == args.image
    assert candidate["RootFS"]["Layers"][:-1] == base["RootFS"]["Layers"]
    version = (
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--read-only",
                "--entrypoint",
                "/opt/fiber/node_modules/.bin/pi",
                args.image,
                "--version",
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    assert version == "0.87.1", version
    runtime_dir = Path(fiber_bundle.__file__).parent / "creation_executor"
    expected_sources = {p.name: digest(p.read_bytes()) for p in runtime_dir.glob("*.mjs")}
    image_sources = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--entrypoint",
            "node",
            args.image,
            "-e",
            "const fs=require('fs'),crypto=require('crypto');console.log(JSON.stringify("
            "Object.fromEntries(['bridge.mjs','draft_tool.mjs'].map(n=>[n,crypto.createHash('sha256')"
            ".update(fs.readFileSync('/opt/fiber/creation/'+n)).digest('hex')]))));",
        ],
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert json.loads(image_sources.stdout) == expected_sources
    cases = {
        "md": {"draft": "# Synthetic draft\n100 psi [source-1]\n"},
        "txt": {"draft": "Synthetic plain text\n100 psi [source-1]\n"},
        "repeat": {"draft": "First immutable draft"},
        "path": {"draft": "text", "path": "../escape.md"},
        "blank": {"draft": " \n\t"},
        "control": {"draft": "invalid\u0000text"},
        "unicode": {"draft": "invalid\ud800text"},
        "existing": {"draft": "overwrite attempt"},
        "symlink": {"draft": "escape attempt"},
    }
    with (
        patch(
            "fiber_bundle.model_gateway.complete", side_effect=AssertionError("provider forbidden")
        ),
        patch(
            "fiber_bundle.execution_config.read_secret",
            side_effect=AssertionError("credential forbidden"),
        ),
    ):
        results = []
        for case, tool_args in cases.items():
            result = qualify(args.image, args.evidence, case, tool_args)
            results.append(result)
            print(case, "PASS", flush=True)
    report = {
        "state": "PASS",
        "image": args.image,
        "base_image": BASE_IMAGE,
        "pi_version": version,
        "runtime_sources": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in runtime_dir.glob("*")
            if p.is_file()
        },
        "qualified_formats": ["md", "txt"],
        "format_check_revision": output.FORMAT_REVISION,
        "scope": "synthetic Pi file capability only; not production model accounting or transport",
        "cases": results,
        "real_provider_calls": 0,
        "main_database_used": False,
        "permanent_activation": False,
    }
    (args.evidence / "qualification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
