"""Run the original read-only package query protocol in its pinned offline runtime."""

import json
import subprocess
from typing import Any
from uuid import uuid4

from . import auth, database, expert_evidence, expert_tasks
from .config import Config, InvalidInput, contained
from .document_capture import reject_duplicate
from .document_worker import remove


def query(
    config: Config, client_id: str, identity: str, question: str, limit: int = 8
) -> dict[str, Any]:
    if (
        not isinstance(question, str)
        or not 1 <= len(question) <= 256
        or type(limit) is not int
        or not 1 <= limit <= 8
    ):
        raise InvalidInput("expert_query_scope")
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            source, packet, _ = expert_evidence.load(config, db, client_id, identity)
            auth.grant(db, client_id, source["vault_id"], "query")
            _, plan, _ = expert_tasks.authorize(config, db, client_id, identity)
            task = expert_tasks.get(db, identity)
            assert task is not None
            capture = db.execute(
                "SELECT capture_id FROM source_expert_attempts WHERE task_id=? AND ordinal=?",
                (identity, task["current_attempt"]),
            ).fetchone()[0]
            root = (
                contained(config.state_root, "source-expert/" + capture) / "output" / packet["root"]
            )
        finally:
            db.close()
    result = run_package(root, plan["policy"]["expert_runtime"]["image"], question, limit)
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            source, current, _ = expert_evidence.load(config, db, client_id, identity)
            auth.grant(db, client_id, source["vault_id"], "query")
            if current != packet:
                raise InvalidInput("expert_query_artifact_changed")
        finally:
            db.close()
    return result  # Preserve every assurance, warning, canonical object and evidence ID.


def run_package(root: Any, image: str, question: str, limit: int) -> dict[str, Any]:
    name = "fiber-document-" + str(uuid4())
    try:
        response = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--read-only",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "128m",
                "--cpus",
                "1",
                "--pids-limit",
                "16",
                "--entrypoint",
                "python",
                "-v",
                str(root) + ":/reference:ro",
                image,
                "-B",
                "/reference/scripts/query_reference.py",
                "--query",
                question,
                "--limit",
                str(limit),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise InvalidInput("knowledge_expert_runtime_unavailable") from error
    finally:
        try:
            remove(name)
        except OSError as error:
            raise InvalidInput("knowledge_expert_runtime_unavailable") from error
    if not 0 < len(response.stdout) <= 1024 * 1024:
        raise InvalidInput("expert_query_result_capacity")
    result = json.loads(response.stdout, object_pairs_hook=reject_duplicate)
    if not isinstance(result, dict):
        raise InvalidInput("expert_query_result")
    return result
