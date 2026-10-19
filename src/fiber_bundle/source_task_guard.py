"""Fixed parent and platform batch authorization for GUI-derived source tasks."""

import json
import sqlite3
from dataclasses import asdict, dataclass

from . import auth
from .config import Config, contained
from .objects import digest
from .store import Store


@dataclass(frozen=True)
class SourceBinding:
    parent_receipt_id: str
    parent_version_id: str
    parent_asset_id: str
    parent_sha256: str
    batch_id: str | None
    account_id: str | None
    submission_id: str
    request_json: str


def bind(db: sqlite3.Connection, run: sqlite3.Row, value: SourceBinding) -> None:
    data = dict(
        run_id=run["id"], client_id=run["client_id"], vault_id=run["vault_id"], **asdict(value)
    )
    db.execute(
        "INSERT INTO source_task_bindings("
        + ",".join(data)
        + ") VALUES("
        + ",".join("?" for _ in data)
        + ")",
        tuple(data.values()),
    )


def authorize(config: Config, db: sqlite3.Connection, run: sqlite3.Row) -> None:
    bound = db.execute("SELECT * FROM source_task_bindings WHERE run_id=?", (run["id"],)).fetchone()
    if bound is None:
        derived = db.execute(
            "SELECT 1 FROM receipts WHERE source_version_id=? AND event_id LIKE 'source-task:%'",
            (run["version_id"],),
        ).fetchone()
        if derived:
            raise auth.Denied(404)
        return
    if (bound["client_id"], bound["vault_id"]) != (run["client_id"], run["vault_id"]):
        raise auth.Denied(404)
    from .source_task_policy import frozen

    frozen(db, run["id"])
    if run["binding_kind"] == "document":
        from .source_imports import authorize as authorize_import

        upload = db.execute("SELECT * FROM source_imports WHERE run_id=?", (run["id"],)).fetchone()
        if upload is None:
            raise auth.Denied(404)
        authorize_import(config, db, upload)
        from .source_task_policy import submission

        if submission(bound["request_json"])["configuration_sha256"] != digest(
            upload["request_json"].encode()
        ):
            raise auth.Denied(404)
    auth.grant(db, bound["client_id"], bound["vault_id"], "add")
    auth.grant(db, bound["client_id"], bound["vault_id"], "original")
    receipt = db.execute(
        "SELECT owner_client_id,source_version_id,state FROM receipts WHERE job_id=?",
        (bound["parent_receipt_id"],),
    ).fetchone()
    if receipt is None or tuple(receipt) != (
        bound["client_id"],
        bound["parent_version_id"],
        "stored",
    ):
        raise auth.Denied(404)
    Store(config).verify_receipt_locked(db, bound["parent_receipt_id"])
    asset = db.execute(
        "SELECT a.sha256 FROM assets a JOIN version_assets va ON va.asset_id=a.id WHERE "
        "va.version_id=? AND a.id=? AND a.vault_id=?",
        (bound["parent_version_id"], bound["parent_asset_id"], bound["vault_id"]),
    ).fetchone()
    if asset is None or asset[0] != bound["parent_sha256"]:
        raise auth.Denied(404)
    if bound["batch_id"] is None:
        return
    path = contained(config.state_root, "weixin-inbox.sqlite3")
    journal = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    journal.row_factory = sqlite3.Row
    try:
        row = journal.execute(
            "SELECT b.state,b.action,h.state AS "
            "store_state,h.results_json,h.results_sha256,x.binding_json FROM wx_batches b "
            "JOIN wx_store_handoffs h ON h.batch_id=b.id JOIN bindings x ON "
            "x.account_id=b.account_id WHERE b.id=? AND b.account_id=?",
            (bound["batch_id"], bound["account_id"]),
        ).fetchone()
        if (
            row is None
            or row["state"] != "selected"
            or row["action"] not in {"save_one", "save_separately"}
            or row["store_state"] != "stored"
        ):
            raise auth.Denied(404)
        identity = json.loads(row["binding_json"])
        if (
            identity["client_id"] != bound["client_id"]
            or identity["vault_id"] != bound["vault_id"]
            or digest(row["results_json"].encode()) != row["results_sha256"]
        ):
            raise auth.Denied(404)
        if not any(
            r["job_id"] == bound["parent_receipt_id"]
            and r["source_version_id"] == bound["parent_version_id"]
            for r in json.loads(row["results_json"])
        ):
            raise auth.Denied(404)
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        raise auth.Denied(404) from None
    finally:
        journal.close()
