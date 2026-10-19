"""Original downloads are limited to receipts owned by the current channel client."""

import json
from typing import Any

from .. import auth, database, objects, vaults
from ..config import Config, InvalidInput
from ..operations import original
from . import intake_repository as repository
from . import store


def receipts(
    config: Config, main: Any, side: Any, identity: str, batch: str
) -> tuple[Any, list[str]]:
    channel, _ = repository.channel(config, identity)
    if side is None:
        raise auth.Denied(404)
    row = side.execute(
        "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?", (identity, batch)
    ).fetchone()
    if row is None or row["state"] != "stored":
        raise auth.Denied(404)
    value = json.loads(row["value"])
    scope = auth.grant(main, channel["value"]["client_id"], value["target_vault_id"], "original")
    vaults.checked_vault(config, scope)
    versions = []
    for receipt in value["receipts"]:
        saved = main.execute(
            "SELECT source_version_id FROM receipts WHERE job_id=? AND owner_client_id=?",
            (receipt["job_id"], channel["value"]["client_id"]),
        ).fetchone()
        if saved is None or saved[0] != receipt["source_version_id"]:
            raise auth.Denied(404)
        versions.append(saved[0])
    return channel, versions


def listing(config: Config, identity: str, batch: str, *, leased: bool = False) -> dict[str, Any]:
    with repository.lease(config, leased), store.readonly(config) as side:
        main = database.connect(config.state_root)
        try:
            _, versions = receipts(config, main, side, identity, batch)
            assets = [
                dict(row)
                for version in versions
                for row in main.execute(
                    "SELECT asset_id,original_name,media_type FROM version_assets "
                    "WHERE version_id=? AND role!='event' ORDER BY ordinal",
                    (version,),
                )
            ]
            return {"assets": assets}
        finally:
            main.close()


def read(
    config: Config, identity: str, batch: str, asset_id: str, *, leased: bool = False
) -> tuple[bytes, str, str]:
    with repository.lease(config, leased), store.readonly(config) as side:
        main = database.connect(config.state_root)
        try:
            channel, versions = receipts(config, main, side, identity, batch)
            for version in versions:
                row = main.execute(
                    "SELECT va.*,a.vault_id FROM version_assets va "
                    "JOIN assets a ON a.id=va.asset_id "
                    "WHERE va.version_id=? AND va.asset_id=? AND va.role!='event'",
                    (version, asset_id),
                ).fetchone()
                if row:
                    data = original(
                        config,
                        main,
                        channel["value"]["client_id"],
                        row["vault_id"],
                        version,
                        asset_id,
                        12 * 1024 * 1024,
                    )
                    return data, row["media_type"], row["original_name"]
            raise auth.Denied(404)
        finally:
            main.close()


def derived(
    config: Config, identity: str, batch: str, index: int, name: str, *, leased: bool = False
) -> tuple[bytes, str]:
    from . import intake_reviews

    with repository.lease(config, leased), store.readonly(config) as side:
        main = database.connect(config.state_root)
        try:
            _, _, value, files = intake_reviews.load(config, main, side, identity, batch, index)
            if name in files:
                import mimetypes

                return files[name], mimetypes.guess_type(name)[0] or "application/octet-stream"
            if name != "source.html":
                raise auth.Denied(404)
            receipt = value["links"][index]["receipt"]
            row = main.execute(
                "SELECT a.* FROM assets a JOIN version_assets va ON va.asset_id=a.id "
                "WHERE va.version_id=? AND va.original_name='source.html'",
                (receipt["source_version_id"],),
            ).fetchone()
            if row is None:
                raise InvalidInput("intake_original_unavailable")
            return objects.read_object(
                config.state_root, row["relative_path"], row["sha256"], row["byte_count"]
            ), "text/html"
        finally:
            main.close()
