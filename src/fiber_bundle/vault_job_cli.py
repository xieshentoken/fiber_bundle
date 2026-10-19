"""Run one already-authorized local Vault job; no service or automatic retry loop."""

import argparse
from pathlib import Path

from .config import load_config
from .vault_job_worker import execute


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    execute(load_config(args.config, args.project_root), args.job_id)


if __name__ == "__main__":
    main()
