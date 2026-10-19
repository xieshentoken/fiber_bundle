"""Explicit offline backup, fresh recovery and single-host activation; no service startup."""

import argparse
import getpass
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from . import backup_bundle, backup_preflight, backup_requests, backup_restore
from .backup_bundle import vault_root
from .backup_inventory import Limits
from .config import InvalidInput, StorageError, load_config, sync_directory
from .inputs import canonical


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("preflight", "create", "restore", "execute", "verify", "activate", "retire"),
    )
    parser.add_argument("--request", help="execute: prepared request UUID")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--report", type=Path, help="new 0600 manifest outside state and Vaults")
    parser.add_argument("--expected-sha256", help="reject a changed previously observed scope")
    parser.add_argument(
        "--destination", type=Path, help="fresh backup directory or restored project"
    )
    parser.add_argument(
        "--bundle", type=Path, help="restore/verify: encrypted local bundle directory"
    )
    parser.add_argument(
        "--include-config", action="append", default=[], help="create: explicit configs/ file"
    )
    parser.add_argument(
        "--confirm-source-stopped",
        action="store_true",
        help="activate: operator confirms sole-host switch",
    )
    parser.add_argument(
        "--authenticate",
        action="store_true",
        help="verify: authenticate every referenced object, with a hidden recovery-key prompt",
    )
    parser.add_argument("--max-entries", type=int, default=None)
    parser.add_argument("--max-file-mb", type=int, default=None)
    parser.add_argument("--max-snapshot-mb", type=int, default=None)
    args = parser.parse_args()
    allowed = {
        "report": {"preflight"},
        "expected_sha256": {"preflight", "create"},
        "destination": {"create", "restore"},
        "bundle": {"restore", "verify"},
        "include_config": {"create"},
        "confirm_source_stopped": {"activate"},
        "request": {"execute"},
        "authenticate": {"verify"},
    }
    for name, commands in allowed.items():
        if getattr(args, name) and args.command not in commands:
            parser.error("--" + name.replace("_", "-") + " is invalid for this command")
    try:
        if any(
            v is not None for v in (args.max_entries, args.max_file_mb, args.max_snapshot_mb)
        ) and args.command not in {"preflight", "create", "restore"}:
            raise InvalidInput("backup_limits_not_applicable")
        defaults = Limits()
        limits = Limits(
            entries=args.max_entries if args.max_entries is not None else defaults.entries,
            file_bytes=args.max_file_mb * 1024**2
            if args.max_file_mb is not None
            else defaults.file_bytes,
            total_bytes=args.max_snapshot_mb * 1024**2
            if args.max_snapshot_mb is not None
            else defaults.total_bytes,
        )
        limits.validate()
        if args.command == "verify":
            if args.bundle is None:
                raise InvalidInput("backup_bundle_required")
            marker = backup_bundle.layout(args.bundle)
            if args.authenticate:
                if not sys.stdin.isatty():
                    raise InvalidInput("backup_interactive_terminal_required")
                password = getpass.getpass("恢复密钥或备份长密码: ")
                from .backup_automation.verification import verify

                verify(args.bundle, password)
            print(
                json.dumps(
                    {
                        "state": "authenticated_verified"
                        if args.authenticate
                        else "ciphertext_verified",
                        "bundle_id": marker["bundle_id"],
                        "scope_sha256": marker["scope_sha256"],
                        "authenticated": args.authenticate,
                        "restorable": False,
                    }
                )
            )
            return 0
        root = args.project_root.resolve(strict=True)
        if args.command in {"create", "restore"}:
            if args.destination is None or not sys.stdin.isatty():
                raise InvalidInput("backup_fresh_destination_and_interactive_terminal_required")
            password = getpass.getpass("备份长密码（至少 16 字符）: ")
            if args.command == "create" and password != getpass.getpass("再次输入: "):
                raise InvalidInput("backup_password_confirmation_differs")
            if args.command == "restore":
                if args.bundle is None:
                    raise InvalidInput("backup_bundle_required")
                result = backup_restore.restore(
                    args.bundle, args.destination, password, limits=limits
                )
                print(json.dumps(result))
                return 0
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        if args.command == "execute":
            if args.request is None or not sys.stdin.isatty():
                raise InvalidInput("backup_request_and_interactive_terminal_required")
            value = backup_requests.read(config, args.request)
            password = getpass.getpass("备份长密码（至少 16 字符）: ")
            if value["action"] == "create" and password != getpass.getpass("再次输入: "):
                raise InvalidInput("backup_password_confirmation_differs")
            print(json.dumps(backup_requests.execute(config, args.request, password)))
            return 0
        if args.command == "create":
            result = backup_bundle.create(
                config,
                args.destination,
                password,
                expected_sha256=args.expected_sha256,
                include_configs=tuple(args.include_config),
                limits=limits,
            )
            print(json.dumps(result))
            return 0
        if args.command == "activate":
            print(
                json.dumps(
                    backup_restore.activate(config, source_stopped=args.confirm_source_stopped)
                )
            )
            return 0
        if args.command == "retire":
            print(json.dumps(backup_restore.retire(config)))
            return 0
        result = (
            backup_preflight.recheck(config, args.expected_sha256, limits=limits)
            if args.expected_sha256 is not None
            else backup_preflight.inspect(config, limits=limits)
        )
        if args.report is not None:
            path = args.report.absolute()
            if path.parent.resolve(strict=True) != path.parent or path.is_symlink():
                raise InvalidInput("backup_report_path_invalid")
            for source in [config.state_root, *(vault_root(config, v) for v in result["vaults"])]:
                if path == source or source in path.parents:
                    raise InvalidInput("backup_report_overlaps_source")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(canonical(result).encode())
                handle.flush()
                os.fsync(handle.fileno())
            sync_directory(path.parent)
        # Do not print filenames, per-client identifiers, receipt payloads or config contents.
        print(
            json.dumps(
                {
                    "state": result["state"],
                    "sha256": result["sha256"],
                    "entries": len(result["entries"]),
                    "file_bytes": result["file_bytes"],
                    "full_system_backup": False,
                    "restorable": False,
                }
            )
        )
        return 0
    except (OSError, ValueError, sqlite3.Error, StorageError) as exc:
        code = str(exc) if isinstance(exc, StorageError) else ""
        if re.fullmatch(r"(?:backup|restore)_[a-z_]{1,80}", code) is None:
            code = "backup_preflight_failed"
        print(json.dumps({"state": "error", "error_type": type(exc).__name__, "error_code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
