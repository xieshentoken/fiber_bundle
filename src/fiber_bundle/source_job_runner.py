"""One immutable source job, independent of the management window."""

import argparse
import json
from pathlib import Path

from . import execution
from .config import Config
from .execution_config import parse_execution_config
from .query_routing_files import read


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        data = json.loads(read(args.configuration, 65536))
        storage = data["storage"]
        config = Config(
            args.project_root,
            state_relative=storage["state_root"],
            vaults=tuple(storage["vaults"].items()),
            vault_roots=tuple(storage.get("vault_roots", {}).items()),
        )
        config.validate()
        policy = parse_execution_config(data["execution"])
        execution.execute(config, policy, args.run_id, "")
        return 0
    except Exception:
        # The durable run ledger contains bounded failure codes, never source or credentials.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
