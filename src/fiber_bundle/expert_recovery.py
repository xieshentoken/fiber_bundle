"""Quarantine abandoned running compiler attempts; preserve artifacts and never replay."""

from . import database
from .config import Config, InvalidInput
from .executor_container import run_lock


def reconcile(config: Config) -> int:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            identities = [
                r[0] for r in db.execute("SELECT id FROM source_expert_tasks WHERE state='running'")
            ]
        finally:
            db.close()
    changed = 0
    for identity in identities:
        try:
            with run_lock(config, identity), database.locked(config):
                db = database.connect(config.state_root)
                try:
                    changed += db.execute(
                        "UPDATE source_expert_tasks SET state='interrupted',launch_state='unknown' "
                        "WHERE id=? AND state='running'",
                        (identity,),
                    ).rowcount
                    db.execute(
                        "UPDATE source_expert_attempts SET state='interrupted' "
                        "WHERE task_id=? AND state='running'",
                        (identity,),
                    )
                finally:
                    db.close()
        except InvalidInput as exc:
            if str(exc) != "execution already owned":
                raise
    return changed
