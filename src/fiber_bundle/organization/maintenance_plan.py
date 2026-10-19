"""Frozen maintenance differences. Existing memberships are not classification evidence."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from .. import vault_save_policy
from ..config import InvalidInput
from ..inputs import canonical
from ..objects import digest
from ..payloads import Payload
from ..vault_scope import inside
from . import classifier, engine, packages, store
from .contracts import ORGANIZER_VERSION
from .manual_relations import ManualRelations

VERSION = "maintenance/v1:" + ORGANIZER_VERSION
MODES = {"quick": "快速整理", "additive": "全量补充", "deep": "深度重整"}


@dataclass
class PlanContext:
    manual: ManualRelations
    decisions: dict[str, list[Any]]


def plan_context(config: Any, vault: str, mocs: Any) -> PlanContext:
    decisions: dict[str, list[Any]] = {}
    for decision in store.rows(config, "decisions", vault):
        decisions.setdefault(decision["entity"], []).append(decision)
    return PlanContext(ManualRelations(mocs), decisions)


class NewMoc(Payload):
    title: str = Field(min_length=1, max_length=180)
    topic: str = Field(min_length=4, max_length=2000)


def analysis_binding(config: Any, db: Any, vault: str) -> dict[str, Any]:
    from ..knowledge_graph import policy as graph_policy
    from . import model_review

    profile = graph_policy.current(config)
    try:
        route = model_review.route(config, db, vault).digest
    except InvalidInput as error:
        route = str(error)
    return {"model": route, "graph": profile.digest if profile else None}


def snapshot(config: Any, db: Any, client: str, vault: str, mode: str) -> Any:
    from . import jobs

    if mode not in MODES:
        raise InvalidInput("maintenance_mode_invalid")
    p = jobs.policy(config, vault)
    settings = {
        k: v
        for k, v in vault_save_policy.get(config, db, vault).items()
        if k not in {"mocs", "mocs_truncated"}
    }
    include = p["settings"]["include"]
    if mode == "quick":
        inbox = settings["settings"]["inbox_dir"]
        include = (
            [inbox]
            if any(inside(inbox, path) for path in include)
            else [path for path in include if inside(path, inbox)]
        )
    if not include:
        raise InvalidInput("maintenance_scope_empty")
    es, ms, _ = jobs.snapshot(
        config,
        db,
        client,
        vault,
        include=include,
        exclude=p["settings"]["exclude"],
        scope_packages=True,
    )
    es = [e for e in es if any(inside(e["path"], base) for base in include)]
    root, allowed = packages.context(config, db, client, vault, write=True)
    epoch = db.execute(
        "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
        (client, vault),
    ).fetchone()
    fixed = {
        "root_key": [root.stat().st_dev, root.stat().st_ino],
        "scope": allowed.wire(),
        "policy": {k: v for k, v in p.items() if k != "last_scan"},
        "save_policy": settings,
        "permission_epoch": epoch[0] if epoch else 0,
        "include": include,
        "analysis_binding": analysis_binding(config, db, vault),
    }
    return es, ms, fixed


def virtual_mocs(vault: str, settings: Any, mocs: Any, proposed: Any) -> Any:
    result = []
    titles = {m["title"].casefold().removesuffix(" moc").strip() for m in mocs}
    from ..vendor.knowledge_organizer_v4 import normalize_list

    for moc in mocs:
        data = engine.note(moc).frontmatter
        titles.update(
            value.casefold().removesuffix(" moc").strip()
            for value in normalize_list(data.get("aliases"))
        )
    for raw in proposed:
        item = NewMoc.model_validate(raw)
        title = item.title.strip()
        if re.search(r'[/\\\x00-\x1f\x7f:*?"<>|]', title) or title in {".", ".."}:
            raise InvalidInput("maintenance_moc_name_invalid")
        if not title.casefold().endswith(" moc"):
            title += " MOC"
        key = title.casefold().removesuffix(" moc").strip()
        if key in titles:
            raise InvalidInput("maintenance_moc_duplicate")
        titles.add(key)
        path = settings["moc_dir"] + "/" + title + ".md"
        mid = str(uuid5(NAMESPACE_URL, "fiber-moc-adoption:" + vault + ":" + path))
        text = "---\ntype: moc\n---\n# " + title + "\n\n" + item.topic.strip() + "\n"
        result.append(
            {
                "id": mid,
                "path": path,
                "title": title,
                "text": text,
                "sha256": digest(text.encode()),
                "members": {},
                "revision": 0,
                "registered": False,
                "new": True,
            }
        )
    return result


def memberships(
    config: Any, vault: str, entity: Any, mocs: Any, context: PlanContext | None = None
) -> Any:
    context = context or plan_context(config, vault, mocs)
    decisions = context.decisions.get(entity["id"], [])
    excluded = {d["moc"] for d in decisions if d["state"] in {"rejected", "retired"}}
    locked = {d["moc"] for d in decisions if d["state"] == "pinned"}
    manual = set(context.manual.memberships(entity)) - excluded
    all_ids = manual | {m["id"] for m in mocs if entity["id"] in m["members"]}
    return sorted((all_ids | locked) - excluded), sorted(manual), sorted(locked), decisions


def item(
    config: Any,
    vault: str,
    entity: Any,
    mocs: Any,
    fixed: Any,
    mode: str,
    *,
    context: PlanContext | None = None,
) -> Any:
    before, manual, locked, decisions = memberships(config, vault, entity, mocs, context)
    rejected = {d["moc"] for d in decisions if d["state"] == "rejected"}
    # Remove only classification links from the inference copy; the real source
    # and its offsets remain intact for semantic/model evidence and CAS checks.
    projected = dict(entity)
    projected["text"] = re.sub(r"(?m)^moc:.*(?:\n[ \t]+[^\n]*)*", "", entity["text"])
    projected["text"] = re.sub(r"\[\[[^\[\]\n]+\]\]", "", projected["text"])
    scored = {}
    for rule in fixed["policy"]["settings"]["rules"]:
        if classifier.matches(entity, rule["match"], rule["conditions"]):
            scored[rule["moc_id"]] = {
                "moc_id": rule["moc_id"],
                "score": 100,
                "reason": "explicit_rule",
            }
    for c in engine.infer(
        projected,
        [
            {**m, "members": {}, "text": m["text"] if mode == "quick" else "# " + m["title"] + "\n"}
            for m in mocs
        ],
    ):
        if c["hub"] not in scored or c["score"] > scored[c["hub"]]["score"]:
            scored[c["hub"]] = {
                "moc_id": c["hub"],
                "score": c["score"],
                "reason": "; ".join(c["reasons"]),
            }
    visible = {m["id"] for m in mocs}
    selected = sorted({mid for mid, c in scored.items() if c["score"] >= 70} & visible - rejected)
    fingerprint = source_fingerprint(entity, mocs, fixed, mode, decisions)
    record = {
        "entity": entity["id"],
        "path": entity["path"],
        "title": entity["title"],
        "kind": entity["kind"],
        "sha256": entity["sha256"],
        "fingerprint": fingerprint,
        "before": before,
        "manual": manual,
        "locked": locked,
        "rejected": sorted(rejected),
        "selected": selected,
        "candidates": list(scored.values()),
        "model_called": False,
        "decisions": decisions,
        "source": {
            k: entity.get(k) for k in ["id", "kind", "path", "sha256", "file_key", "publication_id"]
        },
        "state": "pending",
        "evaluated": bool(selected) or (mode == "quick" and bool(set(before) & visible)),
        "existing_reliable": bool(set(before) & visible),
        "phase": "analysis",
    }
    return difference(record, mode, fixed["save_policy"]["settings"])


def source_fingerprint(entity: Any, mocs: Any, fixed: Any, mode: str, decisions: Any) -> str:
    """Bind original inputs without rerunning inference or changing a frozen result."""
    profile = [
        {"id": m["id"], "text": re.sub(r"<!-- fiber-moc:.*?end -->", "", m["text"], flags=re.S)}
        for m in mocs
    ]
    return digest(
        canonical(
            [VERSION, mode, entity["id"], entity["sha256"], profile, fixed, decisions]
        ).encode()
    )


def difference(value: Any, mode: str, settings: Any) -> Any:
    before = set(value["before"])
    inferred = set(value["selected"]) - set(value["rejected"])
    reliable = bool(inferred) or (mode == "quick" and value.get("existing_reliable", False))
    after = (inferred | set(value["locked"])) if mode == "deep" and reliable else before | inferred
    value.update(
        after=sorted(after),
        add=sorted(after - before),
        remove=sorted(before - after),
        status="linked" if reliable else "review",
    )
    target = None
    if mode == "quick" and reliable and after and inside(value["path"], settings["inbox_dir"]):
        folder = settings["book_dir"] if value["kind"] == "expert" else settings["archive_dir"]
        target = folder + "/" + PurePosixPath(value["path"]).name
    value["target"] = target
    return value


def seal(job: Any) -> str:
    return digest(
        canonical(
            {
                k: job[k]
                for k in ["id", "mode", "client_id", "vault_id", "fixed", "items", "new_mocs"]
            }
            | ({"cache_recovery": job["cache_recovery"]} if job.get("cache_recovery") else {})
        ).encode()
    )
