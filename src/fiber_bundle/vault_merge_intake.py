"""Read legacy channel publication sidecars while preserving receipts and reviews."""

import json
from typing import Any

from . import publication_file_io, publication_locations
from .channels import store
from .config import Config
from .objects import digest


def publications(
    config: Config, db: Any, vault: str, root: Any, scope: Any
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    with store.readonly(config) as side:
        if side is None:
            return result
        for row in side.execute(
            "SELECT channel_id,batch_id,value FROM channel_intake_records ORDER BY "
            "channel_id,batch_id"
        ):
            value = json.loads(row["value"])
            if value.get("target_vault_id") != vault:
                continue
            for index, link in enumerate(value.get("links", [])):
                pub = link.get("publication")
                if (
                    not pub
                    or pub["state"] != "published"
                    or publication_locations.get(db, "intake", pub["id"])
                ):
                    continue
                tree = publication_file_io.tree(root, pub["relative_path"], scope)
                result.append(
                    {
                        "kind": "intake",
                        "id": pub["id"],
                        "entry": pub["relative_path"] + "/note.md",
                        "package": pub["relative_path"],
                        "mapping": {
                            logical: pub["relative_path"] + "/" + logical for logical in tree
                        },
                        "body_files": ["note.md"],
                        "location_revision": 0,
                        "location": None,
                        "original_hashes": {
                            logical: descriptor["sha256"]
                            for logical, descriptor in link["files"].items()
                        },
                        "intake_source": {
                            "source_version_id": link["receipt"]["source_version_id"],
                            "source_url": link.get("url"),
                            "review_id": link["review"]["id"] if link.get("review") else None,
                            "review_type": "human",
                            "review_actor": "local_admin",
                        },
                        "sidecar_audit": {
                            "channel_id": row["channel_id"],
                            "batch_id": row["batch_id"],
                            "index": index,
                            "link_sha256": digest(json.dumps(link, sort_keys=True).encode()),
                        },
                    }
                )
    return result
