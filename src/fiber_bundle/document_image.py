"""Prepare an offline Docker context from an existing, operator-selected pypdf runtime."""

import argparse
import ast
import hashlib
import json
import shutil
from pathlib import Path

from .document_contract import DocumentError

MODULES = (
    "__init__.py",
    "config.py",
    "inputs.py",
    "objects.py",
    "document_contract.py",
    "document_source.py",
    "document_capacity.py",
    "pdf_documents.py",
    "office_documents.py",
    "chat_documents.py",
    "literal_documents.py",
    "archive_members.py",
    "download_names.py",
    "feishu_archives.py",
    "weixin_events.py",
    "material_contract.py",
    "material_geometry.py",
    "document_ocr.py",
    "document_conversion.py",
    "office_conversion.py",
    "image_decoding.py",
    "ocr_documents.py",
    "document_capture.py",
    "document_worker_entry.py",
    "vendor/__init__.py",
    "vendor/fake_expert_pdf.py",
    "vendor/fake-expert-source.json",
)


def prepare(site_packages: Path, destination: Path, *, ocr: bool = False) -> dict[str, object]:
    """Copy only fixed code, pypdf Python sources and its license; never install dependencies."""
    if site_packages.is_symlink() or site_packages.resolve() != site_packages:
        raise DocumentError("document_dependency_path")
    if destination.exists() or destination.is_symlink():
        raise DocumentError("document_context_exists")
    if destination.parent.resolve() != destination.parent or not destination.parent.is_dir():
        raise DocumentError("document_context_path")
    dependency = site_packages / "pypdf"
    license_path = site_packages / "pypdf-6.10.0.dist-info/licenses/LICENSE"
    version = dependency / "_version.py"
    if not version.is_file() or version.is_symlink() or version.resolve() != version:
        raise DocumentError("document_dependency_path")
    try:
        syntax = ast.parse(version.read_text(encoding="utf-8"))
        assignments = [n for n in syntax.body if isinstance(n, ast.Assign)]
        if (
            len(assignments) != 1
            or len(assignments[0].targets) != 1
            or not isinstance(assignments[0].targets[0], ast.Name)
            or assignments[0].targets[0].id != "__version__"
            or ast.literal_eval(assignments[0].value) != "6.10.0"
        ):
            raise DocumentError("document_dependency_pin")
    except (OSError, ValueError, SyntaxError) as exc:
        raise DocumentError("document_dependency_pin") from exc
    sources = [
        (p, "pypdf/" + p.relative_to(dependency).as_posix())
        for p in sorted(dependency.rglob("*.py"))
    ]
    here = Path(__file__).parent.resolve()
    sources += [(here / name, "fiber_bundle/" + name) for name in MODULES]
    sources += [
        (
            here / ("document_executor/Dockerfile.ocr" if ocr else "document_executor/Dockerfile"),
            "Dockerfile",
        ),
        (license_path, "PYPDF-LICENSE"),
    ]
    provenance = json.loads((here / "vendor/fake-expert-source.json").read_text())
    if (
        hashlib.sha256((here / "vendor/fake_expert_pdf.py").read_bytes()).hexdigest()
        != (provenance["adapted_sha256"])
    ):
        raise DocumentError("document_vendor_pin")
    # Validate the whole copy set before creating output. No symlink traversal or caches.
    for source, _ in sources:
        if not source.is_file() or source.is_symlink() or source.resolve() != source:
            raise DocumentError("document_context_source")
    if not (dependency / "__init__.py").is_file():
        raise DocumentError("document_dependency_incomplete")
    destination.mkdir(mode=0o700)
    hashes = {}
    for source, relative in sources:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest: dict[str, object] = {
        "schema": 1,
        "pypdf": "6.10.0",
        "fake_expert": provenance,
        "files_sha256": hashes,
        "dependency_downloaded": False,
        "ocr_dependencies_install_at_build": ocr,
    }
    (destination / "context.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-packages", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ocr", action="store_true")
    arguments = parser.parse_args()
    try:
        prepare(arguments.site_packages.absolute(), arguments.output.absolute(), ocr=arguments.ocr)
    except (DocumentError, OSError, ValueError):
        parser.exit(1, "document_context_failed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
