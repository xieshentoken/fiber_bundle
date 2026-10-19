"""Explicit finite document worker; default invocation performs no storage or provider I/O."""

import argparse
import json
import signal
from pathlib import Path
from typing import Any

from .config import StorageError, load_config
from .document_creation_config import load
from .document_creation_runner import recover_synthetic
from .document_creation_worker import drain


def _interrupt(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--creation-config", type=Path)
    parser.add_argument("--client-id")
    parser.add_argument("--task-id")
    parser.add_argument("--max-tasks", type=int, default=0)
    parser.add_argument("--allow-inference", action="store_true")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        if not args.recover and (not args.allow_inference or args.max_tasks == 0):
            print(json.dumps({"state": "disabled", "processed": 0}))
            return 0
        if not args.client_id or not args.task_id:
            raise ValueError()
        config = load_config(args.config, args.project_root)
        result: dict[str, Any] | list[dict[str, Any]]
        if args.recover:
            result = recover_synthetic(config, args.client_id, args.task_id)
        else:
            if args.creation_config is not None:
                model_policy = load(args.creation_config)
            else:
                from dataclasses import replace

                from .document_creation_config import CreationExecutionConfig
                from .model_configuration.policy import resolve
                from .model_configuration.runtime import expected_image, manifest

                selected = replace(resolve(config), image=expected_image("creation"))
                model_policy = CreationExecutionConfig(
                    selected, manifest()["creation_revision"], ("md", "txt")
                )
            result = drain(
                config,
                args.client_id,
                args.task_id,
                model_policy,
                max_tasks=args.max_tasks,
                allow_provider=True,
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"state": "interrupted", "automatic_replay": False}))
        return 130
    except (StorageError, OSError, ValueError):
        print(json.dumps({"state": "blocked", "automatic_replay": False}))
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
