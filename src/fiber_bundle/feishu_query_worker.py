"""One owned finite Feishu evidence worker; a crash leaves its durable no-replay claim."""

import argparse
import json
import signal
from pathlib import Path

from . import feishu_queries, weixin_task_dispatch
from .config import Config
from .execution_config import parse_execution_config
from .query_routing_files import read
from .weixin_events import Binding


def interrupted(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(read(args.configuration, 65536))
    storage = data["storage"]
    config = Config(
        args.project_root,
        state_relative=storage["state_root"],
        vaults=tuple(storage["vaults"].items()),
        vault_roots=tuple(storage.get("vault_roots", {}).items()),
    )
    binding = Binding(**data["binding"])
    policy = parse_execution_config(data["execution"])
    if policy.max_calls != 1 or policy.max_attempts != 1:
        return 1
    weixin_task_dispatch.dispatch(
        config,
        binding,
        policy,
        maximum=1,
        reserve=lambda db, run: feishu_queries.reserve(
            config, binding, data["authorization_id"], db, run
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
