"""Install a fixed offline component; never download, configure accounts or start it."""

import argparse
import json
from pathlib import Path

from .config import StorageError, load_config
from .runtime_components import install


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root", type=Path, required=True)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--manifest-sha256", required=True)
    args = p.parse_args()
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(root / "config.test.json", root)
        print(json.dumps(install(config, args.bundle, args.manifest_sha256)))
        return 0
    except (OSError, ValueError, StorageError) as exc:
        print(json.dumps({"state": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
