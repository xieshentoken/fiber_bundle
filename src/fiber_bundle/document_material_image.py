"""Build context for explicitly selected, pinned decoder wheels and offline Office tools."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from .document_contract import DocumentError
from .document_image import prepare as native_context
from .inputs import canonical


def prepare(site_packages: Path, decoder_packages: Path, output: Path) -> dict[str, object]:
    base = subprocess.run(
        ["docker", "image", "inspect", "fiber-document-m8:remaining", "--format", "{{.Id}}"],
        check=True,
        timeout=10,
        capture_output=True,
    )
    if (
        base.stdout.strip()
        != b"sha256:a9b0fdb5a65d92a2afd0a1834868c4bd78587445c168fee82ac9ea58878d68a4"
    ):
        raise DocumentError("material_base_image_drift")
    if decoder_packages.resolve() != decoder_packages or decoder_packages.is_symlink():
        raise DocumentError("material_dependency_path")
    pins = {"pillow": "12.3.0", "pillow_heif": "1.8.0", "olefile": "0.47"}
    for name, version in pins.items():
        metadata = decoder_packages / f"{name}-{version}.dist-info/METADATA"
        if not metadata.is_file() or f"Version: {version}\n" not in metadata.read_text():
            raise DocumentError("material_dependency_version")
    paths = sorted(
        p for p in decoder_packages.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    )
    if not paths or sum(p.stat().st_size for p in paths) > 64 * 1024 * 1024:
        raise DocumentError("material_dependency_size")
    for path in paths:
        if path.resolve() != path or path.is_symlink():
            raise DocumentError("material_dependency_path")
    manifest = native_context(site_packages, output)
    hashes = {}
    for path in paths:
        relative = path.relative_to(decoder_packages).as_posix()
        target = output / "decoder" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / "decoder-files.json").write_text(canonical(hashes))
    dockerfile = Path(__file__).parent / "document_executor/Dockerfile.material"
    shutil.copyfile(dockerfile, output / "Dockerfile")
    manifest.update(
        base_image=base.stdout.decode().strip(),
        decoder_versions=pins,
        decoder_files_sha256=hashes,
        conversion_install_at_build=True,
        runtime_network="none",
    )
    (output / "context.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest
