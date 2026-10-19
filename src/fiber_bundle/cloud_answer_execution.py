"""Explicit, single-run cloud editor; the storage lock never spans the network."""

from typing import Any

from . import auth, database, execution_scheduler
from . import cloud_answer_gateway as gateway
from . import cloud_answer_tasks as tasks
from .config import Config, InvalidInput, StorageError
from .executor_container import run_lock
from .local_query_process import LocalCallStopped
from .model_gateway import ModelFailure
from .query_routing_config import QueryRoutingConfig


def execute(
    config: Config,
    route: QueryRoutingConfig,
    client_id: str,
    run_id: str,
    *,
    allow_inference: bool = False,
) -> dict[str, Any]:
    if allow_inference is not True:
        raise InvalidInput("cloud_inference_not_enabled")
    route.validate()
    with execution_scheduler.admit(config, "interactive"), run_lock(config, run_id):
        owned: tasks.Attempt | None = None
        output: dict[str, Any] | None = None
        try:
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    owned = tasks.claim(config, db, route, client_id, run_id)
                finally:
                    db.close()

            def should_stop() -> bool:
                with database.locked(config):
                    db = database.connect(config.state_root)
                    try:
                        run, _ = tasks.get(db, client_id, run_id)
                        if run["state"] != "running":
                            return True
                        assert owned is not None
                        return tasks.current(config, db, route, client_id, run_id) != owned.answer
                    except (auth.Denied, StorageError):
                        return True
                    finally:
                        db.close()

            try:
                output = gateway.complete(
                    owned.policy,
                    run_id,
                    owned.answer,
                    allow_inference=True,
                    should_stop=should_stop,
                )
            except Exception as exc:
                reason = (
                    "cloud_authority_changed"
                    if isinstance(exc, LocalCallStopped)
                    else exc.code
                    if isinstance(exc, ModelFailure) and exc.code in tasks.REASONS
                    else "cloud_transport_failed"
                )
                with database.locked(config):
                    db = database.connect(config.state_root)
                    try:
                        return tasks.fail(db, route, owned, reason)
                    finally:
                        db.close()
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    return tasks.finish(config, db, route, owned, output)
                finally:
                    db.close()
        except (KeyboardInterrupt, SystemExit):
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    tasks.stop(db, client_id, run_id, interrupt=True)
                    # A response may already exist when interruption lands between
                    # return, accounting and persistence. Settle it without delivery.
                    if owned is not None and output is not None:
                        call = db.execute(
                            "SELECT state FROM model_calls WHERE id=? AND run_id=?",
                            (owned.call_id, run_id),
                        ).fetchone()
                        if call is not None and call[0] in ("reserved", "uncertain"):
                            tasks.finish(config, db, route, owned, output)
                finally:
                    db.close()
            raise


def cancel(config: Config, client_id: str, run_id: str) -> dict[str, Any]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            return tasks.stop(db, client_id, run_id)
        finally:
            db.close()


def recover(config: Config, client_id: str, run_id: str) -> dict[str, Any]:
    # Refuse an active executor. After host loss, preserve an unknown attempt;
    # this ledger recovery does not prove the provider or an orphan child stopped.
    with run_lock(config, run_id):
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                return tasks.stop(db, client_id, run_id, interrupt=True)
            finally:
                db.close()
