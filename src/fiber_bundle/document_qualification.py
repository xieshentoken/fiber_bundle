"""Explicit synthetic OCR availability check in one offline, nonprivileged container.

It creates an operator-selected runtime receipt, never a factual accuracy/Gold claim.
No installation, model API, Vault discovery or source-task side effect.
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .document_capture import reject_duplicate
from .document_contract import MAX_SOURCE_BYTES, DocumentError
from .document_ocr import verify_runtime
from .document_worker import DocumentPolicy
from .inputs import canonical
from .material_contract import sha

# Fixed script, no source-controlled code or arguments. Repeats inside one Python process;
# Tesseract is invoked once per repeat and loads the explicitly installed language data.
PROBE = """
import json
from pathlib import Path
from fiber_bundle.ocr_documents import inventory, extract
from fiber_bundle.material_contract import sha
from fiber_bundle.inputs import canonical
source = Path('/qualification/source').read_bytes()
files = inventory()
runtime = {'runtime_files_sha256': files, 'languages': 'eng+chi_sim+chi_tra'}
attempts = []
for ordinal in (1, 2, 3):
    work = Path('/tmp') / str(ordinal)
    work.mkdir()
    value = extract(source, 'image', runtime, Path('/qualification/source'), work)
    unit = value['segments'][0]
    if not unit['text'].strip() or not unit['locator']['boxes']:
        raise ValueError('synthetic probe produced no located text')
    attempts.append({'ordinal': ordinal, 'state': 'passed',
                     'text_sha256': unit['text_sha256'],
                     'boxes_sha256': sha(canonical(unit['locator']['boxes']).encode()),
                     'render_sha256': unit['locator']['render_sha256']})
print(canonical({'runtime_files_sha256': files, 'attempts': attempts}))
"""


def qualify(image: str, fixture: Path, output: Path) -> dict[str, Any]:
    DocumentPolicy(image, 180).validate()
    if fixture.resolve() != fixture or fixture.is_symlink() or not fixture.is_file():
        raise DocumentError("ocr_qualification_fixture")
    if not 0 < fixture.stat().st_size <= MAX_SOURCE_BYTES:
        raise DocumentError("ocr_qualification_fixture")
    source = fixture.read_bytes()
    from .ocr_documents import dimensions

    dimensions(source)
    if output.exists() or output.is_symlink() or output.parent.resolve() != output.parent:
        raise DocumentError("ocr_qualification_output")
    # Own private snapshot eliminates a caller modifying fixture bytes during the probe.
    with tempfile.TemporaryDirectory(prefix="fiber-ocr-qualification-") as directory:
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
            timeout=180,
            check=True,
        )
    if len(completed.stdout) > 16384:
        raise DocumentError("ocr_qualification_result")
    observed = json.loads(completed.stdout, object_pairs_hook=reject_duplicate)
    qualification = {
        "schema": "fiber-ocr-availability/v1",
        "image": image,
        "fixture_sha256": sha(source),
        "runtime_files_sha256": observed["runtime_files_sha256"],
        "attempts": observed["attempts"],
        "scope": "synthetic_availability",
        "accuracy_verified": False,
    }
    receipt = {
        "schema": "fiber-ocr-runtime/v1",
        "image": image,
        "backend": "tesseract/v1",
        "languages": "eng+chi_sim+chi_tra",
        "qualification": qualification,
        "qualification_sha256": sha(canonical(qualification).encode()),
        "runtime_files_sha256": observed["runtime_files_sha256"],
        "accuracy_verified": False,
    }
    verify_runtime(receipt)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(canonical(receipt) + "\n")
    output.chmod(0o600)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        qualify(args.image, args.fixture.absolute(), args.output.absolute())
    except (DocumentError, OSError, ValueError, subprocess.SubprocessError, KeyError):
        parser.exit(1, "ocr_qualification_failed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
