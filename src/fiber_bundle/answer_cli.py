"""Create a bound evidence answer; execute separately via fiber-execute."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import answer_tasks, auth, database, execution_ledger
from .config import StorageError, load_config
from .execution_config import load_execution_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--execution-config", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    binding = parser.add_mutually_exclusive_group(required=True)
    binding.add_argument("--receipt-id")
    binding.add_argument("--vault-id")
    binding.add_argument("--vault-ids", nargs="+")
    binding.add_argument("--default-vaults", action="store_true")
    parser.add_argument("--session-id")
    parser.add_argument("--question-file", type=Path, required=True)
    parser.add_argument("--retrieval-query", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--mode", choices=("extractive", "cited_draft"), default="extractive")
    args = parser.parse_args()
    if bool(args.receipt_id) != bool(args.session_id):
        parser.error("--session-id is required only with --receipt-id")
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        policy = load_execution_config(args.execution_config)
        data: (
            answer_tasks.AnswerInput
            | answer_tasks.NoteAnswerInput
            | answer_tasks.ScopedNoteAnswerInput
        )
        question = args.question_file.read_text()
        if args.receipt_id:
            data = answer_tasks.AnswerInput(
                args.receipt_id,
                args.session_id,
                question,
                args.retrieval_query,
                args.limit,
                mode=args.mode,
            )
        elif args.default_vaults or args.vault_ids:
            data = answer_tasks.ScopedNoteAnswerInput(
                None if args.default_vaults else tuple(args.vault_ids),
                question,
                args.retrieval_query,
                args.limit,
                mode=args.mode,
            )
        else:
            data = answer_tasks.NoteAnswerInput(
                args.vault_id, question, args.retrieval_query, args.limit, mode=args.mode
            )
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                client = auth.client(db, "Bearer " + args.token_file.read_text().strip())
                vault_id, version_id = answer_tasks.scope(config, db, client["id"], data)
                run_id = execution_ledger.create_run(
                    config,
                    db,
                    policy,
                    client["id"],
                    vault_id,
                    version_id,
                    answer=data,
                )
            finally:
                db.close()
        print(json.dumps({"run_id": run_id, "state": "queued"}))
        return 0
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(json.dumps({"status": "error", "error_code": "answer_creation_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
