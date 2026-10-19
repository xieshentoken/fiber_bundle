"""Capture, compare or check one authorized Vault's local note metadata; no write-back."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import auth, note_identities, note_snapshots
from .config import StorageError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "compare", "check", "track", "rebind"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--vault-id", required=True)
    parser.add_argument("--snapshot-id")
    parser.add_argument("--note-identity")
    parser.add_argument("--note-path")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--revision", type=int)
    args = parser.parse_args()
    registering = args.action in {"track", "rebind"}
    if registering:
        if args.note_path is None or args.expected_sha256 is None or args.snapshot_id is not None:
            parser.error("track/rebind require --note-path and --expected-sha256, no snapshot")
        if (args.action == "rebind") != (
            args.note_identity is not None and args.revision is not None
        ):
            parser.error("rebind requires --note-identity and --revision")
        if args.action == "track" and (args.note_identity is not None or args.revision is not None):
            parser.error("track does not accept identity or revision")
    else:
        if (args.action == "capture") != (args.snapshot_id is None):
            parser.error("compare/check require --snapshot-id; capture does not accept it")
        if (args.action == "check") != (args.note_identity is not None):
            parser.error("only check requires --note-identity")
        if (
            args.note_path is not None
            or args.expected_sha256 is not None
            or args.revision is not None
        ):
            parser.error("snapshot commands do not accept registration arguments")
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        with args.token_file.open("rb") as stream:
            token = stream.read(257)
        if len(token) > 256:
            raise StorageError("client token too large")
        if registering:
            assert args.note_path is not None and args.expected_sha256 is not None
            result = note_identities.run(
                config,
                "Bearer " + token.decode().strip(),
                args.vault_id,
                args.action,
                args.note_path,
                args.expected_sha256,
                identity=args.note_identity,
                revision=args.revision,
            )
        else:
            result = note_snapshots.run(
                config,
                "Bearer " + token.decode().strip(),
                args.vault_id,
                args.action,
                snapshot_id=args.snapshot_id,
                note_identity=args.note_identity,
            )
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result.get("status") == "conflict" else 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_code": "note_identity_rejected"
                    if registering
                    else "note_snapshot_rejected",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
