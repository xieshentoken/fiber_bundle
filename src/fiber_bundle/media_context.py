"""Build a small explicit media context without state, Vaults or provider credentials."""

import argparse
import hashlib
import json
from pathlib import Path

MODULES = (
    "__init__.py",
    "config.py",
    "media_contract.py",
    "media_subtitles.py",
    "media_files.py",
    "media_candidate.py",
    "media_capture.py",
    "media_worker_entry.py",
)


def build(output: Path) -> dict[str, str]:
    root = Path(__file__).resolve().parent
    if output.absolute() != output.absolute().resolve() or output.exists():
        raise ValueError("new canonical context directory required")
    output.mkdir(mode=0o700)
    (output / "fiber_bundle").mkdir(mode=0o700)
    hashes = {}
    for name in MODULES:
        raw = (root / name).read_bytes()
        (output / "fiber_bundle" / name).write_bytes(raw)
        hashes["fiber_bundle/" + name] = hashlib.sha256(raw).hexdigest()
    raw = (root / "media_executor/Dockerfile").read_bytes()
    (output / "Dockerfile").write_bytes(raw)
    hashes["Dockerfile"] = hashlib.sha256(raw).hexdigest()
    (output / "context.json").write_text(json.dumps(hashes, sort_keys=True, indent=2))
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.output.absolute())


if __name__ == "__main__":
    main()
