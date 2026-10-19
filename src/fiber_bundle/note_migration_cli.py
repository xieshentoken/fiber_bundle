"""Preflight, explicitly prepare/apply, or reconcile a scoped historical-note migration."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import auth, note_migration, note_migration_execution
from .config import StorageError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "plan",
            "check",
            "prepare",
            "prepare-reviewed",
            "prepare-publication",
            "apply",
            "reconcile",
            "status",
            "resume",
            "project-mocs",
        ),
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--source-vault-id", required=True)
    parser.add_argument("--target-vault-id", required=True)
    parser.add_argument("--note-identity")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--source-path")
    parser.add_argument("--target-path")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--plan-id")
    parser.add_argument("--execution-id")
    parser.add_argument("--target-moc-id")
    parser.add_argument("--moc-revision", type=int)
    parser.add_argument("--request-id")
    args = parser.parse_args()
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        with args.token_file.open("rb") as stream:
            token = stream.read(257)
        if len(token) > 256:
            raise StorageError("client token too large")
        if args.action not in {"plan", "check"}:
            if args.action != "prepare-publication" and any(
                v is not None
                for v in (
                    args.note_identity,
                    args.revision,
                    args.source_path,
                    args.target_path,
                    args.expected_sha256,
                )
            ):
                raise StorageError("execution requires a fixed intent")
            result = note_migration_execution.run(
                config,
                "Bearer " + token.decode().strip(),
                args.source_vault_id,
                args.target_vault_id,
                args.action,
                plan_id=args.plan_id,
                execution_id=args.execution_id,
                target_moc_id=args.target_moc_id,
                moc_revision=args.moc_revision,
                request_id=args.request_id,
                note_identity=args.note_identity,
                revision=args.revision,
                source_path=args.source_path,
                target_path=args.target_path,
                expected_sha256=args.expected_sha256,
            )
        else:
            if any(
                v is not None
                for v in (args.execution_id, args.target_moc_id, args.moc_revision, args.request_id)
            ):
                raise StorageError("preflight does not accept an execution intent")
            result = note_migration.run(
                config,
                "Bearer " + token.decode().strip(),
                args.source_vault_id,
                args.target_vault_id,
                args.action,
                note_identity=args.note_identity,
                revision=args.revision,
                source_path=args.source_path,
                target_path=args.target_path,
                expected_sha256=args.expected_sha256,
                plan_id=args.plan_id,
            )
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result["status"] == "conflict" else 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        code = (
            "note_migration_preflight_rejected"
            if args.action in {"plan", "check"}
            else "note_migration_execution_rejected"
        )
        print(json.dumps({"status": "error", "error_code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
