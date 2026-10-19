"""Register MOC metadata, link stable notes or inspect a same-Vault Markdown preview."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import auth, moc_associations, moc_files
from .config import StorageError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "create",
            "show",
            "link",
            "unlink-system",
            "plan-file",
            "apply-file",
            "reconcile-file",
        ),
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--vault-id", required=True)
    parser.add_argument("--moc-id", required=True)
    parser.add_argument("--title")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--note-identity")
    parser.add_argument("--owner", choices=("manual", "system"))
    parser.add_argument("--expected-sha256")
    parser.add_argument("--moc-path")
    parser.add_argument("--plan-id")
    args = parser.parse_args()
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        with args.token_file.open("rb") as stream:
            token = stream.read(257)
        if len(token) > 256:
            raise StorageError("client token too large")
        if args.action in {"plan-file", "apply-file", "reconcile-file"}:
            if any(v is not None for v in (args.title, args.note_identity, args.owner)):
                raise StorageError("MOC file actions do not accept association arguments")
            result = moc_files.run(
                config,
                "Bearer " + token.decode().strip(),
                args.vault_id,
                args.action,
                args.moc_id,
                revision=args.revision,
                relative=args.moc_path,
                expected_sha256=args.expected_sha256,
                plan_id=args.plan_id,
            )
        else:
            if args.moc_path is not None or args.plan_id is not None:
                raise StorageError("MOC association actions do not accept file arguments")
            result = moc_associations.run(
                config,
                "Bearer " + token.decode().strip(),
                args.vault_id,
                args.action,
                args.moc_id,
                title=args.title,
                revision=args.revision,
                note_identity=args.note_identity,
                owner=args.owner,
                expected_sha256=args.expected_sha256,
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(json.dumps({"status": "error", "error_code": "moc_association_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
