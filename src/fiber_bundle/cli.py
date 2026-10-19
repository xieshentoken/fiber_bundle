"""Local storage administration and explicit loopback HTTP server."""

import argparse
import getpass
import json
import sqlite3
import sys
from dataclasses import asdict, fields
from pathlib import Path

from fiber_bundle.config import StorageError, load_config
from fiber_bundle.database import SCHEMA_VERSION, initialize, migrate
from fiber_bundle.execution_config import load_execution_config
from fiber_bundle.http_guard import Settings
from fiber_bundle.store import Store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("init", "check", "snapshot", "migrate", "set-password", "serve")
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument(
        "--execution-config", type=Path, help="serve: enable queued answer submission"
    )
    for field in fields(Settings):
        parser.add_argument(
            "--" + field.name.replace("_", "-"),
            type=str if field.name == "host" else int,
            default=field.default,
        )
    args = parser.parse_args()
    if args.execution_config is not None and args.command != "serve":
        parser.error("--execution-config is only supported by serve")
    try:
        args.project_root = args.project_root.resolve(strict=True)
        config_path = args.config if args.config.is_absolute() else args.project_root / args.config
        config = load_config(config_path, args.project_root)
        if args.command == "init":
            initialize(config)
            print(json.dumps({"status": "initialized", "schema_version": SCHEMA_VERSION}))
        elif args.command == "migrate":
            backup = migrate(config)
            print(json.dumps({"schema_version": SCHEMA_VERSION, "migrated": backup is not None}))
        elif args.command == "set-password":
            from fiber_bundle.auth import set_password

            if not sys.stdin.isatty():
                raise StorageError("interactive terminal required")
            password = getpass.getpass("管理密码（至少 12 字符）: ")
            if password != getpass.getpass("再次输入: "):
                raise StorageError("password confirmation differs")
            set_password(config, password)
            print(json.dumps({"password_set": True, "previous_sessions_revoked": True}))
        elif args.command == "serve":
            import uvicorn

            from fiber_bundle.api import create_app

            settings = Settings(
                **{field.name: getattr(args, field.name) for field in fields(Settings)}
            )
            settings.validate()
            uvicorn.run(
                create_app(
                    config,
                    settings,
                    execution_policy=load_execution_config(args.execution_config)
                    if args.execution_config is not None
                    else None,
                ),
                host=settings.host,
                port=settings.port,
                proxy_headers=False,
                access_log=False,
                server_header=False,
                workers=1,
                timeout_keep_alive=5,
            )
        elif args.command == "check":
            receipts = Store(config).recover()
            print(json.dumps({"receipts": [asdict(item) for item in receipts]}))
            return int(any(item.state != "stored" for item in receipts))
        else:
            path = Store(config).snapshot()
            print(
                json.dumps(
                    {
                        "metadata_snapshot": path.relative_to(config.state_root).as_posix(),
                        "full_system_backup": False,
                    }
                )
            )
    except (OSError, sqlite3.Error, StorageError) as exc:
        # Filesystem/SQLite exceptions may contain input or local paths: emit the type only.
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
