"""Build a fresh Python candidate with public source/package provenance, never deploy it."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from fiber_bundle.build_identity import candidate
from fiber_bundle.config import InvalidInput, contained
from fiber_bundle.inputs import canonical

ROOT_FILES = (
    "README.md",
    "docs/TECHNICAL_REFERENCE.md",
    "docs/STATUS.md",
    "docs/HANDOFF.md",
    "docs/SPEC.md",
    "docs/DECISIONS.md",
    "docs/ARCHITECTURE.md",
    "MANIFEST.in",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "eslint.config.js",
    "ui-policy.json",
    "src/fiber_bundle/knowledge_executor/requirements.lock",
    "src/fiber_bundle/knowledge_executor/SEMANTICA-LICENSE",
    "tools/build_knowledge_graph_runtime.py",
)
SUFFIXES = {
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ttf",
    ".json",
    ".html",
    ".css",
    ".txt",
    ".md",
    ".toml",
    ".typed",
    ".swift",
    ".rst",
    ".png",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".mp4",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            value.update(data)
    return value.hexdigest()


def selected(root: Path) -> list[Path]:
    """Explicit public trees only; no state, Vaults, evidence, profiles or user config."""
    paths = [root / name for name in ROOT_FILES]
    for folder in ("src/fiber_bundle", "tests", "desktop", "frontend/graph"):
        for p in contained(root, folder).rglob("*"):
            if any(
                part in {"__pycache__", "node_modules"} or part.endswith(".egg-info")
                for part in p.relative_to(root).parts
            ):
                continue
            if p.is_symlink():
                raise InvalidInput("candidate_source_symlink")
            if p.is_file() and (
                p.suffix in SUFFIXES
                or p.name in {"Dockerfile", "LICENSE"}
                or p.name.startswith("Dockerfile.")
            ):
                if p.name == "build_identity.json":
                    raise InvalidInput("candidate_generated_identity_in_source")
                paths.append(p)
    paths.extend(contained(root, "configs").glob("*.example.json"))
    # Skill assets are public, versioned input too; preserve their complete tree,
    # including extensionless resources that the application snapshot validates.
    for folder in ("skills/fake-expert", "skills/bili-note-V3"):
        for p in contained(root, folder).rglob("*"):
            if any(part in {"__pycache__", ".DS_Store"} for part in p.parts) or p.suffix == ".pyc":
                continue
            if p.is_symlink():
                raise InvalidInput("candidate_source_symlink")
            if p.is_file():
                paths.append(p)
    for p in paths:
        if p.resolve(strict=True) != p or p.stat().st_nlink != 1:
            raise InvalidInput("candidate_source_not_independent")
    return sorted(paths)


def snapshot(root: Path, destination: Path) -> dict[str, str]:
    paths = selected(root)
    before = {str(p.relative_to(root)): digest(p) for p in paths}
    for p in paths:
        target = destination / p.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, target)
        if digest(target) != before[str(p.relative_to(root))]:
            raise InvalidInput("candidate_source_changed")
    if selected(root) != paths or any(digest(root / name) != sha for name, sha in before.items()):
        raise InvalidInput("candidate_source_changed")
    return before


def build(root: Path, output: Path) -> dict[str, object]:
    root, output = root.absolute(), output.absolute()
    if root.resolve(strict=True) != root or not root.is_dir():
        raise InvalidInput("candidate_source_path_invalid")
    if (
        output.exists()
        or output.is_symlink()
        or output.parent.resolve(strict=True) != output.parent
    ):
        raise InvalidInput("candidate_fresh_output_required")
    if output == root or output.is_relative_to(root) or root.is_relative_to(output):
        raise InvalidInput("candidate_output_overlaps_source")
    output.mkdir(mode=0o700)
    sources = snapshot(root, output / "source")
    source = output / "source"
    version = tomllib.loads((source / "pyproject.toml").read_text())["project"]["version"]
    identity = candidate(version, sources)
    (source / "src/fiber_bundle/build_identity.json").write_text(canonical(identity) + "\n")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(output / "packages")],
        cwd=source,
        capture_output=True,
        text=True,
        timeout=180,
    )
    (output / "build.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise InvalidInput("candidate_build_failed")
    artifacts = sorted((output / "packages").iterdir())
    if len(artifacts) != 2 or {p.suffix for p in artifacts} != {".whl", ".gz"}:
        raise InvalidInput("candidate_artifacts_invalid")
    report: dict[str, object] = {
        "schema": "candidate-build/v1",
        "identity": identity,
        "source_files": sources,
        "artifacts": {p.name: {"sha256": digest(p), "bytes": p.stat().st_size} for p in artifacts},
        "deployed": False,
        "frozen_release_modified": False,
    }
    (output / "candidate-build.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.source, args.out)
    print(json.dumps({k: v for k, v in report.items() if k != "source_files"}))


if __name__ == "__main__":
    main()
