"""Container-only full compiler CLI, fixed verbs and no model/network fallback."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import InvalidInput
from .document_capture import reject_duplicate
from .expert_runtime import selection
from .inputs import canonical
from .objects import digest

SKILL = Path("/opt/skill")


def validate_runtime(expected: str) -> None:
    raw = Path("/opt/skill-files.json").read_bytes()
    if digest(raw) != expected:
        raise InvalidInput("expert_skill_manifest_changed")
    for name, sha in json.loads(raw, object_pairs_hook=reject_duplicate).items():
        path = SKILL / name
        if path.resolve() != path or path.is_symlink() or digest(path.read_bytes()) != sha:
            raise InvalidInput("expert_skill_changed")


def cli(arguments: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-B", str(SKILL / "scripts/fake_expert.py"), *arguments, "--json"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=270,
    )
    if not 0 < len(completed.stdout) <= 1024 * 1024:
        raise InvalidInput("expert_cli_result")
    value = json.loads(completed.stdout, object_pairs_hook=reject_duplicate)
    if not isinstance(value, dict):
        raise InvalidInput("expert_cli_result")
    return value


def source_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(256 * 1024):
            value.update(data)
    return value.hexdigest()


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read(16385), object_pairs_hook=reject_duplicate)
        if (
            not isinstance(request, dict)
            or not {"schema", "command", "profile", "backend", "skill_sha256", "workpack", "limits"}
            <= set(request)
            <= {
                "schema",
                "command",
                "profile",
                "backend",
                "skill_sha256",
                "workpack",
                "limits",
                "figures",
            }
            or request["schema"] != 1
            or request["command"] not in {"start", "resume"}
            or type(request["workpack"]) is not bool
        ):
            raise InvalidInput("expert_worker_request")
        selection(request["profile"], request["backend"])
        validate_runtime(request["skill_sha256"])
        source = Path("/document/input/source.pdf")
        output = Path("/document/output")
        limits = request["limits"]
        if source.stat().st_size > limits["source_bytes"]:
            raise InvalidInput("expert_source_capacity")
        if request["profile"] == "native-text":
            from pypdf import PdfReader  # type: ignore[import-not-found]

            pdf = PdfReader(source, strict=True)
            if pdf.is_encrypted or not 1 <= len(pdf.pages) <= limits["pdf_pages"]:
                raise InvalidInput("expert_page_capacity")
            if (
                sum(len((page.extract_text() or "").encode()) for page in pdf.pages)
                > limits["text_bytes"]
            ):
                raise InvalidInput("expert_text_capacity")
        workspace = output / "workspace"
        job = workspace / "fake-expert-job.json"
        doctor = cli(
            ["doctor", "--source", str(source), "--profile", request["profile"]]
            + (["--backend", request["backend"]] if request["backend"] else [])
        )
        if request["command"] == "start":
            args = [
                "plan",
                "--source",
                str(source),
                "--workspace",
                str(workspace),
                "--profile",
                request["profile"],
                "--name",
                "imported-reference",
                "--display-name",
                "Imported Reference Draft",
                "--domain",
                "imported-reference",
                "--target-stage",
                "draft",
                "--gold-mode",
                "none",
            ]
            if "figures" in request:
                if request["figures"] not in {"none", "index", "index+images"}:
                    raise InvalidInput("expert_worker_request")
                args += ["--figures", request["figures"]]
            if request["backend"]:
                args += ["--backend", request["backend"]]
            if request["workpack"]:
                args += ["--workpack", "/document/input/workpack"]
            planned = cli(args)
            if not job.is_file():
                raise InvalidInput("expert_plan_failed")
            if planned.get("source_sha256") != source_hash(source):
                raise InvalidInput("expert_source_changed")
        elif not job.is_file():
            raise InvalidInput("expert_resume_missing")
        result = cli(["compile" if request["command"] == "start" else "resume", "--job", str(job)])
        inspected = cli(["status", "--job", str(job)])
        if (
            result.get("status") not in {"paused", "completed", "rejected"}
            or inspected.get("status") != result["status"]
        ):
            raise InvalidInput("expert_status_changed")
        validated = None
        if result["status"] == "completed":
            sys.path.insert(0, str(SKILL / "scripts"))
            from direct_reference_skill import (  # type: ignore[import-not-found]
                validate_direct_reference,
            )

            packet = json.loads(job.read_bytes())
            package = Path(packet["outputs"]["skill"])
            if not package.is_relative_to(workspace) or validate_direct_reference(package):
                raise InvalidInput("expert_canonical_validation_failed")
            validated = True
        value = {
            "schema": "fiber-expert-capture/v1",
            "source_sha256": source_hash(source),
            "skill_sha256": request["skill_sha256"],
            "doctor": doctor,
            "result": result,
            "status": inspected,
            "knowledge_verified": False,
            "model_calls": 0,
            "artifact_validation": validated,
        }
        target = output / ("result.json" if request["command"] == "start" else "resume-result.json")
        if target.exists():
            raise InvalidInput("expert_capture_exists")
        # The output is private candidate data; no result field authorizes publication.
        target.write_bytes(canonical(value).encode())
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.stderr.write("expert_capture_failed\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
