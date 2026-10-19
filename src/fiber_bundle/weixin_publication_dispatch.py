"""Default-off publication attempts, separate from task execution and message delivery."""

from typing import Any

from . import database, execution_ledger
from . import weixin_inbox as inbox
from . import weixin_publication as publication
from .config import Config, InvalidInput
from .weixin_events import Binding


def dispatch(config: Config, binding: Binding, *, maximum: int = 0) -> dict[str, Any]:
    if type(maximum) is not int or not 0 <= maximum <= 100:
        raise InvalidInput("publication limit must be an integer from 0 to 100")
    result: dict[str, Any] = {"state": "disabled", "processed": 0, "results": []}
    if maximum == 0:
        return result
    for _ in range(maximum):
        with inbox.connection(config, binding) as journal:
            inbox.bound(journal, binding)
            if not journal.execute(
                "SELECT 1 FROM sqlite_master WHERE name='wx_publication_batches'"
            ).fetchone():
                result["state"] = "idle"
                return result
            rows = journal.execute(
                "SELECT p.* FROM wx_task_publications p JOIN wx_publication_batches b "
                "ON b.batch_id=p.batch_id WHERE b.account_id=? AND p.phase!='published' "
                "ORDER BY p.rowid",
                (binding.account_id,),
            ).fetchall()
            db = database.connect(config.state_root)
            try:
                target = None
                for row in rows:
                    publication.planned_locked(journal, binding, row["batch_id"])
                    if row["phase"] != "pending":
                        result.update(state="blocked", blocked_run_id=row["run_id"])
                        return result
                    run = execution_ledger.get_run(db, row["run_id"])
                    execution_ledger.authorize(config, db, run)
                    if run["state"] == "succeeded":
                        target = row["run_id"]
                        break
                    if run["state"] in {"failed", "cancelled", "interrupted"}:
                        result.update(state="blocked", blocked_run_id=row["run_id"])
                        return result
                if target is None:
                    result["state"] = "idle"
                    return result
                result["processed"] += 1
                try:
                    published = publication.publish_locked(config, journal, db, binding, target)
                except Exception:
                    result.update(state="stopped", blocked_run_id=target)
                    return result
                result["results"].append(published)
            finally:
                db.close()
    result["state"] = "limit_reached"
    return result
