import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fiber_bundle import auth, database, note_inventory
from fiber_bundle.config import StorageError
from fiber_bundle.knowledge_graph import baseline
from fiber_bundle.knowledge_graph.intake import Selection, deliver, prepare, revalidate

from .support import FIXTURE, result


def file_hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_disabled_does_not_open_storage_or_load_tool(config):
    before = file_hashes(config.project_root)
    with patch.object(database, "connect", side_effect=AssertionError("must stay disabled")):
        with pytest.raises(StorageError, match="knowledge_graph_disabled"):
            prepare(config, "no credential", "no vault", ())
    assert file_hashes(config.project_root) == before


def test_selected_snapshot_has_no_paths_credentials_or_unselected_content(corpus_config, corpus):
    authorization, vaults, root, selected, _ = corpus
    sentinel = root / "unselected.md"
    sentinel.write_text("# synthetic unselected\nEXCLUDED_BODY_CANARY\n")
    before = file_hashes(corpus_config.project_root)
    try:
        prepared = prepare(
            corpus_config, authorization, vaults["personal"], selected[:1], enabled=True
        )
        payload = prepared.request.encode()
        assert b"EXCLUDED_BODY_CANARY" not in payload
        assert b"Bearer" not in payload and str(root).encode() not in payload
        assert len(prepared.request.notes) == 1
        assert prepared.request.notes[0].source_kind == "historical_note"
        assert prepared.request.notes[0].source_version_id is None
        assert file_hashes(corpus_config.project_root) == before
    finally:
        sentinel.unlink()


def test_denied_vault_is_rejected_before_inventory_content_read(corpus_config, corpus):
    authorization, vaults, _, selected, _ = corpus
    with patch.object(note_inventory, "read", side_effect=AssertionError("unauthorized read")):
        with pytest.raises(auth.Denied):
            prepare(corpus_config, authorization, vaults["supplier"], selected, enabled=True)


def test_no_path_fallback_for_unregistered_or_unknown_identity(corpus_config, corpus):
    authorization, vaults, _, selected, _ = corpus
    for bad in [
        Selection("path:sample-01.md", selected[0].expected_sha256),
        Selection("note:00000000-0000-0000-0000-000000000000", "a" * 64),
    ]:
        with pytest.raises(StorageError):
            prepare(corpus_config, authorization, vaults["personal"], (bad,), enabled=True)


@pytest.mark.parametrize("change", ["edit", "delete", "rename", "replace"])
def test_changed_source_cannot_deliver_old_result(corpus_config, corpus, prepared, change):
    authorization, _, root, _, _ = corpus
    binding = prepared.bindings[0]
    path = root / binding.relative_path
    saved = path.with_name("saved-original.fixture")
    contents = path.read_bytes()
    if change == "edit":
        path.write_bytes(contents + b"\nchanged\n")
    else:
        path.rename(saved)
        if change == "replace":
            path.write_bytes(contents)
    try:
        with pytest.raises(StorageError):
            deliver(
                corpus_config,
                authorization,
                prepared,
                json.dumps(result(prepared.request)).encode(),
                enabled=True,
            )
    finally:
        if change == "edit":
            path.write_bytes(contents)
        else:
            path.unlink(missing_ok=True)
            saved.rename(path)


def test_revocation_and_module_disable_block_delivery(corpus_config, corpus, prepared):
    authorization, vaults, _, _, _ = corpus
    raw = json.dumps(result(prepared.request)).encode()
    with pytest.raises(StorageError, match="knowledge_graph_disabled"):
        deliver(corpus_config, authorization, prepared, raw)
    db = database.connect(corpus_config.state_root)
    try:
        auth.set_grant(
            db,
            prepared.request.client_id,
            vaults["personal"],
            query=False,
            original=False,
            add=True,
        )
        with pytest.raises(auth.Denied):
            deliver(corpus_config, authorization, prepared, raw, enabled=True)
    finally:
        auth.set_grant(
            db, prepared.request.client_id, vaults["personal"], query=True, original=False, add=True
        )
        db.close()


def test_different_authorized_reader_does_not_inherit_task(corpus_config, corpus, prepared):
    _, vaults, _, _, _ = corpus
    db = database.connect(corpus_config.state_root)
    other = auth.create_client(db, "synthetic second reader", False)
    auth.set_grant(db, other["id"], vaults["personal"], query=True, original=False, add=False)
    db.close()
    with pytest.raises(StorageError):
        revalidate(corpus_config, "Bearer " + other["token"], prepared, enabled=True)


def test_readonly_delivery_and_baseline_use_actual_existing_search(corpus_config, corpus, prepared):
    authorization, _, root, _, fixture = corpus
    before = file_hashes(corpus_config.project_root)
    parsed = deliver(
        corpus_config,
        authorization,
        prepared,
        json.dumps(result(prepared.request)).encode(),
        enabled=True,
    )
    assert parsed.review_state == "suggested"
    report = baseline.run(
        corpus_config,
        authorization,
        prepared,
        tuple(q["query"] for q in fixture["questions"]),
        enabled=True,
    )
    assert report["method"] == "existing_search.search" and report["top_k"] == 10
    assert report["tags"]["state"] == report["moc_associations"]["state"] == "N/A"
    assert report["graph_quality"] == "NOT RUN" and report["model_calls"] == 0
    outcomes = {item["query"]: item for item in report["results"]}
    assert outcomes["微通道冷板"]["status"] == "evidence_found"
    assert outcomes["MC-CP"]["results"][0]["source_kind"] == "historical_note"
    assert outcomes["量子真空泵"]["status"] == "no_evidence"
    assert outcomes["AB-123"]["results"] == []
    assert file_hashes(corpus_config.project_root) == before
    assert len(list(root.glob("*.md"))) == 60
    assert not (corpus_config.state_root / "knowledge-graph.sqlite3").exists()
    assert len(fixture["notes"]) == 60
    assert {note["split"] for note in fixture["notes"]} == {"tuning", "validation"}


def test_baseline_does_not_silently_filter_unselected_existing_hits(corpus_config, corpus):
    authorization, vaults, _, selected, _ = corpus
    subset = prepare(corpus_config, authorization, vaults["personal"], selected[:1], enabled=True)
    with pytest.raises(StorageError, match="baseline_contains_unselected"):
        baseline.run(corpus_config, authorization, subset, ("青松供应商",), enabled=True)


def test_module_import_and_app_creation_do_not_import_semantica(config, store):
    import builtins

    from fiber_bundle.api import create_app

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("semantica"):
            raise AssertionError("optional tool must not be imported by base app")
        return original(name, *args, **kwargs)

    with patch("builtins.__import__", guarded):
        import fiber_bundle.knowledge_graph.intake  # noqa: F401

        assert create_app(config) is not None


def test_frozen_corpus_is_local_and_has_no_pretend_quality_measurement():
    fixture = json.loads(FIXTURE.read_text())
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == (
        "acb9d381bbb466645438da0f2626a3036f9aabd4d437c16632a6bccf13bc6793"
    )
    assert fixture["source"] == "fixed synthetic corpus; no real Vault data"
    assert fixture["annotation_status"].endswith("NOT RUN")
    assert len(fixture["notes"]) == 60 and len(fixture["questions"]) == 12
    assert len(fixture["annotations"]) == 5
    assert all(note["source_kind"] == "historical_note" for note in fixture["notes"])
    assert not Path("tests/knowledge_graph/fixtures/credentials.json").exists()
