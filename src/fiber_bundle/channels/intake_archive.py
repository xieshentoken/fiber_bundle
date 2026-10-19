"""One durable archival operation; interrupted writes require explicit recovery."""

import json

from .. import database, weixin_batches, weixin_inbox, weixin_store
from ..config import Config, InvalidInput
from ..weixin_store_dispatch import worker_lock
from . import intake_repository as repository
from . import store
from .intake_processing import guard_locked, record


def archive(
    config: Config, identity: str, batch: str, revision: int, *, manual: bool = False
) -> None:
    channel, _ = repository.channel(config, identity)
    from ..intake_abort_store import guard as abort_guard

    abort_guard(config, {"channel": identity, "batch": batch})
    bound = repository.binding(channel)
    with worker_lock(config):
        with weixin_inbox.connection(config, bound) as journal, store.opened(config) as side:
            policy = guard_locked(config, identity, revision, manual=manual)
            saved = side.execute(
                "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
                (identity, batch),
            ).fetchone()
            if saved is None or saved["state"] != "queued":
                raise InvalidInput("intake_record_not_queued")
            value = json.loads(saved["value"])
            if value["target_vault_id"] != policy["settings"]["target_vault_id"]:
                raise InvalidInput("intake_target_changed")
            view = weixin_batches.snapshot(journal, bound, batch)
            recovering = value.get("recovering", False)
            selected = manual and value.get("choice") == view.get("action")
            if (
                view["state"] != "frozen"
                and not ((recovering or selected) and view["state"] == "selected")
            ) or not view["content_ready"]:
                with database.transaction(side):
                    record(side, identity, batch, revision, "manual", "intake_batch_changed", value)
                return
            with database.transaction(side):
                consumed = side.execute(
                    "SELECT 1 FROM channel_intake_consumption WHERE channel_id=? "
                    "AND kind='store' AND target=?",
                    (identity, batch),
                ).fetchone()
                if not (
                    (recovering and consumed)
                    or repository.reserve(side, identity, policy["settings"], "store", batch)
                ):
                    record(
                        side,
                        identity,
                        batch,
                        revision,
                        "manual",
                        "intake_store_budget_exhausted",
                        value,
                    )
                    return
                record(side, identity, batch, revision, "archiving", None, value)
            if view["state"] == "frozen":
                with database.transaction(journal):
                    weixin_batches.choose_in_transaction(
                        journal,
                        bound,
                        batch,
                        value.get("choice")
                        or (
                            "save_one"
                            if policy["settings"]["grouping"] == "batch"
                            else "save_separately"
                        ),
                    )
            phase = "original_archive"
            originals_saved = False
            try:

                def checked() -> None:
                    guard_locked(config, identity, revision, manual=manual)

                result = weixin_store.transfer(
                    config,
                    bound,
                    batch,
                    guard=checked,
                    target_vault_id=value["target_vault_id"],
                    leased=True,
                )
                value["receipts"] = result["receipts"]
                originals_saved = True
                phase = "original_projection"
                # Only new archival work enters staged notifications. Historical
                # sent/unknown receipts keep their original deduplication contract.
                value.setdefault("receipt_tracking", {"version": 2, "next_check": 0})
                from ..attachment_projection import archive_version

                with database.connect(config.state_root) as main:
                    value["original_projections"] = [
                        projection
                        for receipt in value["receipts"]
                        if receipt.get("source_version_id")
                        for projection in archive_version(
                            config,
                            main,
                            bound.client_id,
                            value["target_vault_id"],
                            receipt["source_version_id"],
                        )
                    ]
                value["content_state"] = "link_only" if value["links"] else "originals_only"
                value["extraction"] = (
                    "pending"
                    if value["links"]
                    and (
                        policy["settings"]["fetch_articles"]
                        or policy["settings"]["fetch_subtitles"]
                    )
                    else "off"
                )
                phase = "processing_admission"
                with database.transaction(side):
                    from uuid import NAMESPACE_URL, uuid5

                    from ..automatic_jobs import enqueue

                    with database.connect(config.state_root) as main:
                        for index, _link in enumerate(value["links"]):
                            if value["extraction"] != "off":
                                enqueue(
                                    config,
                                    main,
                                    bound.client_id,
                                    value["target_vault_id"],
                                    "intake",
                                    str(
                                        uuid5(
                                            NAMESPACE_URL,
                                            f"fiber-intake-auto:{identity}:{batch}:{index}",
                                        )
                                    ),
                                    {
                                        "intake": {
                                            "channel": identity,
                                            "batch": batch,
                                            "index": index,
                                            "revision": revision,
                                        }
                                    },
                                )
                        from ..automatic_channel_sources import admit

                        value["channel_batch"] = {"channel": identity, "batch": batch}
                        abort_guard(config, value["channel_batch"])
                        admit(config, main, bound.client_id, value["target_vault_id"], value)
                    record(side, identity, batch, revision, "stored", None, value)

            except Exception as error:
                from .intake_failures import capture

                value["failure"] = capture(error, phase, originals_saved)
                with database.transaction(side):
                    record(
                        side,
                        identity,
                        batch,
                        revision,
                        "needs_review",
                        value["failure"]["code"],
                        value,
                    )
                raise
