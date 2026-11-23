"""A bound supplemental proof admits one harmless entry without rebasing old evidence."""

import copy
import json
from contextlib import ExitStack

import pytest
from test_committed_verification import evidence, interrupted, no_replay
from test_jobs import ready as ready

from fiber_bundle import auth, database, publication_file_io, vault_scope
from fiber_bundle.archive_link_planning import LinkPlanningSession
from fiber_bundle.config import IntegrityError
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.organization import maintenance_committed_verification as verification


def proof(config, db, job, root, relative):
    operation = next(
        item["archive_operation"] for item in job["items"] if item.get("archive_operation")
    )
    row = db.execute("SELECT payload FROM vault_operations WHERE id=?", (operation,)).fetchone()
    value = json.loads(row[0])
    scope = vault_scope.for_vault(root, auth.grant(db, job["client_id"], job["vault_id"], "query"))
    names, _ = LinkPlanningSession().candidates(root, scope, value.get("read_omissions", {}))
    inverse = {new: old for old, new in value["moves"].items()}
    current = sorted(inverse.get(name, name) for name in names)
    raw, key = publication_file_io.read(root, relative)
    info = root.stat()
    return {
        "schema": "committed-namespace-addition/v1",
        "operation_id": operation,
        "payload_sha256": digest(row[0].encode()),
        "root_key": [info.st_dev, info.st_ino],
        "original_namespace_sha256": value["link_observation"]["path_sha256"],
        "current_namespace_sha256": digest(canonical(current).encode()),
        "addition": {"path": relative, "sha256": digest(raw), "file_key": key, "size": len(raw)},
    }


def test_approved_addition_finishes_original_without_payload_or_pending_changes(config, ready):
    job = interrupted(config, ready, managed_note=True)
    root = ready[2]
    relative = "Resource/synthetic-approved-receipt.txt"
    (root / relative).write_bytes(b"synthetic exact added original input snapshot only")
    before = evidence(root)
    pending = copy.deepcopy([item for item in job["items"] if item["state"] == "pending"])
    with database.locked(config), database.connect(config.state_root) as db, ExitStack() as stack:
        addition = proof(config, db, job, root, relative)
        operation = addition["operation_id"]
        payload = db.execute(
            "SELECT payload FROM vault_operations WHERE id=?", (operation,)
        ).fetchone()[0]
        token = verification.projection(config, db, job, supplemental_proof=addition)["plan_sha256"]
        assert token != verification.projection(config, db, job)["plan_sha256"]
        no_replay(stack)
        result = verification.apply(config, db, job, token, supplemental_proof=addition)
        receipt = result["archive_verifications"][-1]
        assert receipt["state"] == "verified", receipt
        assert receipt["supplemental_proof"] == addition
        assert result["state"] == "partial" and result["error"] == job["error"]
        assert [item for item in result["items"] if item["state"] == "pending"] == pending
        assert (
            db.execute("SELECT payload FROM vault_operations WHERE id=?", (operation,)).fetchone()[
                0
            ]
            == payload
        )
        assert verification.apply(config, db, result, token, supplemental_proof=addition) == result
        with pytest.raises(IntegrityError, match="maintenance_verification_state_changed"):
            verification.apply(config, db, result, token)
    assert evidence(root) == before


@pytest.mark.parametrize(
    "change",
    [
        "no_proof",
        "unapproved_second",
        "edited_note",
        "replaced_inode",
        "referenced",
        "revoked",
        "uncommitted",
        "payload",
        "namespace_hash",
    ],
)
def test_addition_proof_rejects_all_other_changes(config, ready, change):
    job = interrupted(config, ready, second=False, managed_note=True)
    root = ready[2]
    relative = "Resource/synthetic-approved-receipt.txt"
    path = root / relative
    path.write_bytes(b"synthetic exact added original input snapshot only")
    with database.locked(config), database.connect(config.state_root) as db:
        addition = proof(config, db, job, root, relative)
        if change == "unapproved_second":
            (root / "Resource/another.txt").write_text("not approved")
        elif change == "edited_note":
            moved = root / job["items"][0]["target"]
            moved.write_text(moved.read_text() + "\nHuman change\n")
        elif change == "replaced_inode":
            replacement = root / ".candidate-replacement"
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        elif change == "referenced":
            moved = root / job["items"][0]["target"]
            moved.write_text(moved.read_text() + "\n[[synthetic-approved-receipt.txt]]\n")
        elif change == "revoked":
            auth.set_grant(
                db, job["client_id"], job["vault_id"], query=True, add=False, original=False
            )
        elif change == "uncommitted":
            value = json.loads(
                db.execute(
                    "SELECT payload FROM vault_operations WHERE id=?", (addition["operation_id"],)
                ).fetchone()[0]
            )
            value["metadata_committed"] = False
            db.execute(
                "UPDATE vault_operations SET payload=? WHERE id=?",
                (canonical(value), addition["operation_id"]),
            )
        elif change == "payload":
            addition["payload_sha256"] = "0" * 64
        elif change == "namespace_hash":
            addition["current_namespace_sha256"] = "0" * 64
        supplied = None if change == "no_proof" else addition
        scope = verification.projection(config, db, job, supplemental_proof=supplied)
        if change == "revoked":
            with pytest.raises(auth.Denied):
                verification.apply(
                    config, db, job, scope["plan_sha256"], supplemental_proof=supplied
                )
        elif change == "uncommitted":
            assert scope["available"] is False
        else:
            result = verification.apply(
                config, db, job, scope["plan_sha256"], supplemental_proof=supplied
            )
            assert result["archive_verifications"][-1]["state"] == "conflict", result[
                "archive_verifications"
            ]
            assert result["state"] == "partial"
            assert result["items"][0]["state"] == job["items"][0]["state"]


@pytest.mark.parametrize(
    "reference",
    [
        "[[synthetic-approved-receipt.txt]]",
        "[receipt](synthetic%2Dapproved%2Dreceipt.txt)",
        '<a href="synthetic-approved-receipt&#46;txt">receipt</a>',
    ],
)
def test_preexisting_reference_to_new_name_rejects_even_without_note_changes(
    config, ready, reference
):
    root = ready[2]
    (root / "Resource/old-inbound.md").write_text("# Prior stationary note\n" + reference + "\n")
    job = interrupted(config, ready, second=False, managed_note=True)
    relative = "Resource/synthetic-approved-receipt.txt"
    (root / relative).write_bytes(b"exact supplemental input")
    with database.locked(config), database.connect(config.state_root) as db:
        addition = proof(config, db, job, root, relative)
        token = verification.projection(config, db, job, supplemental_proof=addition)["plan_sha256"]
        result = verification.apply(config, db, job, token, supplemental_proof=addition)
        assert result["archive_verifications"][-1]["state"] == "conflict"
        assert (
            result["archive_verifications"][-1]["error"]
            == "placement_namespace_addition_referenced"
        )


def test_two_readonly_verifications_reuse_bodies_but_recheck_current_names(config, ready):
    import time

    from fiber_bundle import placement_committed
    from fiber_bundle.note_read_session import reuse

    job = interrupted(config, ready, second=False, managed_note=True)
    root = ready[2]
    relative = "Resource/synthetic-approved-receipt.txt"
    (root / relative).write_bytes(b"exact supplemental input")
    with database.connect(config.state_root) as db, reuse() as session:
        addition = proof(config, db, job, root, relative)
        value = json.loads(
            db.execute(
                "SELECT payload FROM vault_operations WHERE id=?", (addition["operation_id"],)
            ).fetchone()[0]
        )
        scope = vault_scope.for_vault(
            root, auth.grant(db, job["client_id"], job["vault_id"], "query")
        )
        started = time.monotonic()
        placement_committed.verify(config, db, value, root, scope, namespace_addition=addition)
        first = dict(session.counts)
        placement_committed.verify(config, db, value, root, scope, namespace_addition=addition)
        second = dict(session.counts)
        assert second["body_reads"] == first["body_reads"]
        assert second["body_reused"] > first["body_reused"]
        assert second["namespace_addition_checks"] == 2
        print(
            json.dumps(
                {
                    "two_verify_seconds": round(time.monotonic() - started, 6),
                    "first": first,
                    "second": second,
                }
            )
        )
        (root / "Resource/unapproved-second.txt").write_text("new")
        with pytest.raises(IntegrityError):
            placement_committed.verify(config, db, value, root, scope, namespace_addition=addition)


def test_nfc_variant_of_preexisting_reference_is_not_admitted(config, ready):
    import unicodedata

    root = ready[2]
    name = "réception-unique.txt"
    (root / "Resource/old-inbound.md").write_text(
        "# Old\n[[" + unicodedata.normalize("NFD", name) + "]]\n"
    )
    job = interrupted(config, ready, second=False, managed_note=True)
    relative = "Resource/" + name
    (root / relative).write_bytes(b"exact supplemental input")
    with database.locked(config), database.connect(config.state_root) as db:
        addition = proof(config, db, job, root, relative)
        token = verification.projection(config, db, job, supplemental_proof=addition)["plan_sha256"]
        result = verification.apply(config, db, job, token, supplemental_proof=addition)
        assert (
            result["archive_verifications"][-1]["error"]
            == "placement_namespace_addition_referenced"
        )


def test_supplement_cannot_cover_multiple_selected_operations(config, ready):
    from uuid import uuid4

    from fiber_bundle.organization import store

    job = interrupted(config, ready, second=False, managed_note=True)
    root = ready[2]
    relative = "Resource/synthetic-approved-receipt.txt"
    (root / relative).write_bytes(b"exact supplemental input")
    with database.locked(config), database.connect(config.state_root) as db:
        addition = proof(config, db, job, root, relative)
        other = str(uuid4())
        original = db.execute(
            "SELECT * FROM vault_operations WHERE id=?", (addition["operation_id"],)
        ).fetchone()
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,?,?,?,?)",
            (
                other,
                original["vault_id"],
                original["kind"],
                original["state"],
                original["payload"],
                original["created_at"],
                original["updated_at"],
            ),
        )
        item = copy.deepcopy(job["items"][0])
        item["archive_operation"] = other
        item["entity"] = "second-owned-item"
        job["items"].append(item)
        store.put(config, "jobs", job["id"], job["vault_id"], job)
        result = verification.projection(config, db, job, supplemental_proof=addition)
        assert (
            result["available"] is False
            and result["conflict"] == "placement_namespace_addition_conflict"
        )
