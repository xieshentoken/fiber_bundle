"""Explicit actual DOC conversion availability receipt; no semantic or Gold certification."""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .document_capture import reject_duplicate
from .document_contract import DocumentError
from .document_conversion import verify_runtime
from .document_worker import DocumentPolicy
from .inputs import canonical
from .material_contract import sha

PROBE = """
from pathlib import Path
from fiber_bundle.office_conversion import inventory, convert
from fiber_bundle.office_documents import extract
from fiber_bundle.material_contract import sha
from fiber_bundle.inputs import canonical
source = Path('/qualification/source').read_bytes()
files = inventory()
attempts = []
for ordinal in (1, 2, 3):
    work = Path('/tmp') / str(ordinal)
    work.mkdir()
    converted = convert(source, {'runtime_files_sha256': files}, work)
    value = extract(converted, 'docx')
    text = '\\n'.join(s['text'] for s in value['segments'])
    if not value['complete'] or not text.strip():
        raise ValueError('synthetic conversion did not provide complete located text')
    attempts.append({'ordinal': ordinal, 'state': 'passed', 'text_sha256': sha(text.encode())})
print(canonical({'runtime_files_sha256': files, 'attempts': attempts}))
"""


def qualify(image: str, fixture: Path, output: Path) -> dict[str, Any]:
    DocumentPolicy(image, 300).validate()
    if (
        fixture.resolve() != fixture
        or fixture.is_symlink()
        or not fixture.is_file()
        or not 0 < fixture.stat().st_size <= 8 * 1024 * 1024
    ):
        raise DocumentError("conversion_qualification_fixture")
    if output.exists() or output.is_symlink() or output.parent.resolve() != output.parent:
        raise DocumentError("conversion_qualification_output")
    source = fixture.read_bytes()
    with tempfile.TemporaryDirectory(prefix="fiber-doc-qualification-") as directory:
        snapshot = Path(directory) / "source"
        snapshot.write_bytes(source)
        snapshot.chmod(0o644)
        Path(directory).chmod(0o755)
        completed = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--network=none",
                "--read-only",
                "--user=1000:1000",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--memory=256m",
                "--cpus=1",
                "--pids-limit=32",
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=32m",
                "--mount=type=bind,src=" + str(snapshot) + ",dst=/qualification/source,readonly",
                "--entrypoint=python",
                image,
                "-c",
                PROBE,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=300,
            check=True,
        )
    if len(completed.stdout) > 16384:
        raise DocumentError("conversion_qualification_result")
    observed = json.loads(completed.stdout, object_pairs_hook=reject_duplicate)
    qualification = {
        "schema": "fiber-doc-availability/v1",
        "image": image,
        "fixture_sha256": sha(source),
        "runtime_files_sha256": observed["runtime_files_sha256"],
        "attempts": observed["attempts"],
        "scope": "synthetic_availability",
        "accuracy_verified": False,
    }
    value = {
        "schema": "fiber-doc-conversion/v1",
        "image": image,
        "runtime_files_sha256": observed["runtime_files_sha256"],
        "qualification": qualification,
        "qualification_sha256": sha(canonical(qualification).encode()),
        "accuracy_verified": False,
    }
    verify_runtime(value)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
    output.chmod(0o600)
    return value
