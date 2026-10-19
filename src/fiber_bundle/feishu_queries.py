"""Explicit owner query scheduling on the existing evidence/cost/task ledgers."""

import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import database, feishu_budgets, weixin_inbox, weixin_store, weixin_task_bindings
from .config import Config, InvalidInput, contained
from .execution_config import ExecutionConfig
from .inputs import canonical
from .query_policy_store import write_private
from .runtime_environment import child_environment
from .weixin_events import Binding


def submit_ready(
    config: Config, binding: Binding, policy: ExecutionConfig, grant: str | None
) -> int:
    """Only selected explicit query/consult plans. Never infer a Vault or recent material."""
    with weixin_inbox.connection(config, binding) as db:
        rows = db.execute(
            "SELECT id FROM wx_batches WHERE account_id=? AND state='selected' "
            "ORDER BY rowid DESC LIMIT 100",
            (binding.account_id,),
        ).fetchall()
    count = 0
    for row in rows:
        from . import weixin_intents

        with weixin_inbox.connection(config, binding) as journal:
            intent = weixin_intents.read_locked(journal, binding, row[0])
            if (
                journal.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='wx_task_bindings'"
                ).fetchone()
                and journal.execute(
                    "SELECT 1 FROM wx_task_bindings WHERE batch_id=?", (row[0],)
                ).fetchone()
            ):
                continue
        if intent and intent.get("query_existing"):
            weixin_store.transfer(config, binding, row[0])
        try:
            view = weixin_store.get(config, binding, row[0])
        except InvalidInput as exc:
            if str(exc) != "no Store handoff":
                raise
            continue
        if view["action"] != "consult" and not view.get("query_after_save"):
            continue
        if not view.get("question"):
            continue
        if view["store_state"] != "stored":
            continue
        value = weixin_task_bindings.submit(config, binding, row[0], policy)
        with weixin_inbox.connection(config, binding) as db, database.transaction(db):
            db.execute(
                "CREATE TABLE IF NOT EXISTS fiber_feishu_query_runs("
                "run_id TEXT PRIMARY KEY REFERENCES wx_task_bindings(run_id),"
                "authorization_id TEXT) STRICT"
            )
            db.execute(
                "INSERT OR IGNORE INTO fiber_feishu_query_runs VALUES(?,?)",
                (value["run_id"], grant),
            )
        count += 1
    return count


def reserve(config: Config, binding: Binding, grant: str | None, db: Any, run_id: str) -> bool:
    row = db.execute(
        "SELECT authorization_id FROM fiber_feishu_query_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    return bool(
        row
        and row[0] == grant
        and feishu_budgets.reserve_locked(db, binding, grant, "model", run_id)
    )


def launch(
    config: Config, binding: Binding, policy: ExecutionConfig, grant: str | None, home: Path
) -> subprocess.Popen[bytes]:
    if policy.max_calls != 1 or policy.max_attempts != 1:
        raise InvalidInput("Feishu queries require one call and no retries")
    path = contained(home, "fiber-query-worker.json")
    write_private(
        path,
        canonical(
            {
                "storage": config.storage_configuration(),
                "execution": asdict(policy),
                "binding": asdict(binding),
                "authorization_id": grant,
            }
        ).encode(),
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "fiber_bundle.feishu_query_worker",
            "--project-root",
            str(config.project_root),
            "--configuration",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=child_environment(),
        start_new_session=True,
    )


def ready(config: Config, binding: Binding, policy: ExecutionConfig, grant: str | None) -> bool:
    with weixin_inbox.connection(config, binding) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fiber_feishu_query_runs'"
        ).fetchone():
            return False
        main = database.connect(config.state_root)
        try:
            for row in db.execute(
                "SELECT t.run_id FROM wx_task_bindings t JOIN "
                "fiber_feishu_query_runs f ON f.run_id=t.run_id "
                "WHERE t.account_id=? AND t.policy_digest=? AND f.authorization_id=?",
                (binding.account_id, policy.digest, grant),
            ):
                run = main.execute(
                    "SELECT state FROM execution_runs WHERE id=?", (row[0],)
                ).fetchone()
                if run and run[0] == "queued":
                    dispatch = db.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='wx_task_dispatches'"
                    ).fetchone()
                    old = (
                        db.execute(
                            "SELECT phase FROM wx_task_dispatches WHERE run_id=?", (row[0],)
                        ).fetchone()
                        if dispatch
                        else None
                    )
                    if not old or old[0] == "observed":
                        return True
            return False
        finally:
            main.close()
