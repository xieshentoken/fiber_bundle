"""Opt-in post-publication supplier work; idle polling never calls a model."""

import json
import logging
import threading
import time

from .. import database
from ..config import Config, InvalidInput
from . import ingestion, model, published, store
from .contracts import DocumentImport


class Runtime:
    def __init__(self, config: Config):
        self.config = config
        self.last_scan: dict[str, float] = {}
        self.fingerprints: dict[str, tuple[int, ...]] = {}
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        with database.locked(self.config):
            with store.connection(self.config, write=False) as side:
                interrupted = (
                    [
                        (r[0], json.loads(r[1]))
                        for r in side.execute("SELECT vault,value FROM records WHERE kind='job'")
                    ]
                    if side
                    else []
                )
            for vault, job in interrupted:
                if job["state"] == "running":
                    job.update(state="failed", error="supplier_interrupted_resume_required")
                    with store.connection(self.config, write=True) as side:
                        assert side is not None
                        store.put(side, vault, "job", job)
            with store.connection(self.config) as side:
                calls = (
                    [
                        (r[0], json.loads(r[1]))
                        for r in side.execute(
                            "SELECT vault,value FROM records WHERE kind='model_call'"
                        )
                    ]
                    if side
                    else []
                )
            for vault, call in calls:
                if call["state"] == "sending":
                    call["state"] = "unknown"
                    with database.connect(self.config.state_root) as db:
                        db.execute(
                            "UPDATE model_configuration_calls SET state='failed',"
                            "error_code='supplier_model_result_unknown' "
                            "WHERE id=? AND state='running'",
                            (call["id"],),
                        )
                    with store.connection(self.config, write=True) as side:
                        assert side is not None
                        store.put(side, vault, "model_call", call)
        self.thread = threading.Thread(target=self.loop, name="supplier-intelligence", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)

    def tick(self) -> None:
        with database.locked(self.config):
            with store.connection(self.config) as side:
                profiles = (
                    [
                        (r[0], json.loads(r[1]))
                        for r in side.execute("SELECT vault,value FROM profiles")
                    ]
                    if side
                    else []
                )
            for vault, policy in profiles:
                if not policy["enabled"]:
                    continue
                activation = store.get(self.config, vault, "activation", "current")
                if not activation:
                    continue
                observation_ok = False
                try:
                    with database.connect(self.config.state_root) as db:
                        fingerprint = published.fingerprint(db, vault)
                        if (
                            fingerprint != self.fingerprints.get(vault)
                            and time.monotonic() - self.last_scan.get(vault, 0) >= 30
                        ):
                            published.enqueue(
                                self.config, db, activation["client_id"], vault, activation["after"]
                            )
                            self.last_scan[vault] = time.monotonic()
                            self.fingerprints[vault] = fingerprint
                        rows = db.execute(
                            "SELECT r.id,r.client_id FROM execution_runs r JOIN "
                            "document_publications p ON p.run_id=r.id WHERE "
                            "r.vault_id=? AND r.created_at>=? AND p.state='published'"
                            " ORDER BY r.created_at,r.id LIMIT 100001",
                            (vault, activation["after"]),
                        ).fetchall()
                        if len(rows) > 100000:
                            raise InvalidInput("supplier_scope_limit")
                        for row in rows:
                            if not store.get(self.config, vault, "job", "document-" + row[0]):
                                ingestion.document(
                                    self.config,
                                    db,
                                    DocumentImport(client_id=row[1], vault_id=vault, run_id=row[0]),
                                )
                                break
                    observation_ok = True
                except Exception as error:
                    code = (
                        str(error)
                        if isinstance(error, InvalidInput)
                        else "supplier_scheduler_failed"
                    )
                    if not code.replace("_", "").isalnum() or len(code) > 100:
                        code = "supplier_scheduler_failed"
                    with store.connection(self.config, write=True) as side:
                        assert side is not None
                        store.put(
                            side, vault, "issue", {"id": "runtime", "state": "open", "reason": code}
                        )
                issue = store.get(self.config, vault, "issue", "runtime")
                if observation_ok and issue and issue.get("state") == "open":
                    issue["state"] = "resolved"
                    with store.connection(self.config, write=True) as side:
                        assert side is not None
                        store.put(side, vault, "issue", issue)
                jobs = store.rows(self.config, vault, "job")
                job = next((j for j in jobs if j["state"] == "queued"), None)
                if job:
                    break
            else:
                return
        if not self.stop.is_set():
            model.run(self.config, vault, job["id"])

    def loop(self) -> None:
        while not self.stop.wait(5):
            try:
                self.tick()
            except Exception as error:
                logging.getLogger(__name__).error(
                    "Supplier scheduler failed (%s)", type(error).__name__
                )
                # Keep a bounded diagnostic without logging source text.
                try:
                    with database.locked(self.config):
                        with store.connection(self.config) as side:
                            vaults = (
                                [r[0] for r in side.execute("SELECT vault FROM profiles")]
                                if side
                                else []
                            )
                        for vault in vaults:
                            with store.connection(self.config, write=True) as side:
                                assert side is not None
                                store.put(
                                    side,
                                    vault,
                                    "issue",
                                    {
                                        "id": "runtime",
                                        "reason": "supplier_scheduler_failed",
                                        "state": "open",
                                        "error_type": type(error).__name__,
                                    },
                                )
                except Exception as reporting_error:
                    logging.getLogger(__name__).error(
                        "Supplier diagnostic failed (%s)", type(reporting_error).__name__
                    )
