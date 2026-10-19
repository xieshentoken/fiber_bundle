"""Publish or reconcile one completed article using its owner's current credential."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import article_publication, auth, database, publication_organization
from .config import StorageError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--organize", action="store_true")
    parser.add_argument("--moc-id")
    parser.add_argument("--moc-revision", type=int)
    parser.add_argument("--moc-path")
    parser.add_argument("--moc-expected-sha256")
    parser.add_argument("--new-moc-title")
    args = parser.parse_args()
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        selection = (args.moc_id, args.moc_revision, args.moc_path, args.moc_expected_sha256)
        if args.organize:
            if any(v is None for v in selection):
                raise StorageError("organization requires a complete explicit MOC selection")
        elif any(v is not None for v in (*selection, args.new_moc_title)):
            raise StorageError("MOC selection requires explicit organization switch")
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                with args.token_file.open("rb") as stream:
                    token = stream.read(257)
                if len(token) > 256:
                    raise StorageError("client token too large")
                client = auth.client(db, "Bearer " + token.decode().strip())
                result = article_publication.publish_locked(config, db, client["id"], args.run_id)
                if args.organize:
                    try:
                        result["organization"] = publication_organization.queue_locked(
                            config,
                            db,
                            client["id"],
                            args.run_id,
                            moc_id=args.moc_id,
                            revision=args.moc_revision,
                            relative=args.moc_path,
                            expected_sha256=args.moc_expected_sha256,
                            new_title=args.new_moc_title,
                        )
                    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
                        result["organization"] = {
                            "state": "blocked",
                            "error_code": "publication_organization_rejected",
                        }
            finally:
                db.close()
        print(json.dumps(result))
        return 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(json.dumps({"status": "error", "error_code": "publication_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
