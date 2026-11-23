"""Candidate accounting from the existing authorized inventory traversal, never a new scan."""

from typing import Any

from .. import publication_inventory_read, publication_locations
from ..vault_scope import Scope, inside

LIMIT = 100


class Observation:
    def __init__(self, scope: Scope, include: list[str], moc_dir: str) -> None:
        self.scope = Scope(
            tuple(
                sorted(
                    {
                        wanted if inside(wanted, base) else base
                        for wanted in include
                        for base in scope.include
                        if inside(wanted, base) or inside(base, wanted)
                    }
                )
            ),
            scope.exclude,
            scope.policy_sha256,
        )
        self.moc_dir = moc_dir
        self.omissions: dict[str, str] = {}

    def accepts(self, path: str) -> bool:
        return self.scope.allows(path) and not inside(path, self.moc_dir)

    def skip(self, path: str, reason: str) -> None:
        if self.accepts(path):
            self.omissions[path] = reason

    def result(self, db: Any, vault: str, entities: list[Any]) -> dict[str, Any]:
        included = {e["path"] for e in entities if self.accepts(e["path"])}
        skipped = {
            path: {"path": path, "reason": reason, "kind": "note"}
            for path, reason in self.omissions.items()
            if path not in included
        }
        # Only enrich paths already encountered in the scoped traversal. The compact
        # ledger projection does not authorize disclosing any other mapped path.
        locations = publication_inventory_read.rows(db, vault) if skipped else ()
        for row, plan in locations:
            relevant = set(plan["mapping"].values()) | {row["package_root"]}
            for path in relevant & skipped.keys():
                item = skipped[path]
                logical = [name for name, mapped in plan["mapping"].items() if mapped == path]
                bodies = plan.get("body_files", plan["mapping"])
                body_names = set(bodies) if isinstance(bodies, (dict, list)) else set()
                if logical and not any(
                    name == "note.md"
                    or name in body_names
                    or plan.get("supplement_roles", {}).get(name) == "analysis"
                    for name in logical
                ):
                    item["reason"] = "publication_internal_file"
                    continue
                if not publication_locations.committed(db, row):
                    item.update(reason="publication_not_committed", publication_state=row["state"])
                elif item["reason"] == "managed_publication_not_visible":
                    item["reason"] = "publication_internal_file"
                if path == row["package_root"]:
                    item["kind"] = "package" if row["kind"] == "expert" else "note"
                    if item["reason"] == "managed_content_hidden":
                        item["reason"] = "publication_internal_file"
        for item in skipped.values():
            if item["reason"] == "expert_package_not_registered":
                item["kind"] = "package"
        # Attachment and retained-source roots are traversal barriers, not note
        # candidates. Do not count their unknown children or expand package members.
        skipped = {
            path: item
            for path, item in skipped.items()
            if item["reason"] not in {"managed_content_hidden", "publication_internal_file"}
        }
        values = [skipped[path] for path in sorted(skipped)]
        return {
            "version": 1,
            "complete": True,
            "unit": "note_or_package",
            "scope": {"include": list(self.scope.include), "exclude": list(self.scope.exclude)},
            "discovered_count": len(included) + len(values),
            "included_count": len(included),
            "skipped_count": len(values),
            "skipped": values[:LIMIT],
            "skipped_truncated": len(values) > LIMIT,
            "skipped_limit": LIMIT,
        }
