"""Copy the complete existing compiler Skill into a fresh immutable offline context."""

import shutil
import subprocess
from pathlib import Path

from .config import InvalidInput
from .document_image import prepare as native_context
from .inputs import canonical
from .objects import digest


def prepare(skill: Path, site_packages: Path, output: Path) -> str:
    observed = subprocess.run(
        ["docker", "image", "inspect", "fiber-document-m8:remaining", "--format", "{{.Id}}"],
        capture_output=True,
        check=True,
        timeout=10,
    )
    if (
        observed.stdout.strip()
        != b"sha256:a9b0fdb5a65d92a2afd0a1834868c4bd78587445c168fee82ac9ea58878d68a4"
    ):
        raise InvalidInput("expert_base_image_changed")
    if skill.resolve() != skill or skill.is_symlink() or not skill.is_dir():
        raise InvalidInput("expert_skill_path")
    version = (skill / "scripts/compiler_version.py").read_text()
    if (
        'RELEASE_VERSION = "1.2.0"' not in version
        or 'UX_CLI_PROTOCOL = "fake-expert-ux-cli-v0.2"' not in version
    ):
        raise InvalidInput("expert_skill_version")
    files = [skill / "SKILL.md", skill / "requirements-compiler.txt"]
    for area in ("scripts", "references", "assets", "agents"):
        files.extend(
            p for p in (skill / area).rglob("*") if p.is_file() and "__pycache__" not in p.parts
        )
    if len(files) > 2000 or sum(p.stat().st_size for p in files) > 32 * 1024 * 1024:
        raise InvalidInput("expert_skill_capacity")
    for path in files:
        if path.resolve() != path or path.is_symlink():
            raise InvalidInput("expert_skill_path")
    native_context(site_packages, output)
    hashes = {}
    for path in sorted(files):
        name = path.relative_to(skill).as_posix()
        target = output / "skill" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[name] = digest(target.read_bytes())
    manifest = canonical(hashes).encode()
    (output / "skill-files.json").write_bytes(manifest)
    here = Path(__file__).parent
    for module in ("expert_worker_entry.py", "expert_runtime.py"):
        shutil.copyfile(here / module, output / "fiber_bundle" / module)
    dockerfile = (output / "Dockerfile").read_text()
    dockerfile = "FROM fiber-document-m8:remaining\n" + dockerfile.split("\n", 1)[1]
    dockerfile = dockerfile.replace(
        "USER 1000:1000",
        "COPY skill /opt/skill\nCOPY skill-files.json /opt/skill-files.json\nUSER 1000:1000",
    )
    (output / "Dockerfile").write_text(
        dockerfile.replace("fiber_bundle.document_worker_entry", "fiber_bundle.expert_worker_entry")
    )
    return digest(manifest)
