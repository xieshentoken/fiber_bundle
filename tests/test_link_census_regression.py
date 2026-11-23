"""Archive link races retry one fresh read while retaining the original write proof."""

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_moc_placement_projection import setup

from fiber_bundle import (
    archive_link_census,
    archive_link_planning,
    cloud_observation,
    database,
    moc_file_journal,
    moc_placement_projection,
    note_placement,
    note_placement_execution,
    note_read_session,
    vault_scope,
)
from fiber_bundle.archive_link_planning import LinkPlanningSession
from fiber_bundle.config import IntegrityError


def touch_directory(root):
    info = root.stat()
    os.utime(root, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))


@contextmanager
def census_change(change, *, repeatedly=False, finalizing=True):
    """Mutate real files after an actual census, without replacing any proof check."""
    original_finalize = moc_placement_projection.finalize
    original_candidates = LinkPlanningSession.candidates
    original_entries = vault_scope.metadata_entries
    scans = []

    def candidates(session, root, scope, omissions):
        def entries(*args, **kwargs):
            yield from original_entries(*args, **kwargs)
            scans.append(root)
            if repeatedly or len(scans) == 1:
                change(root)

        with patch.object(vault_scope, "metadata_entries", entries):
            return original_candidates(session, root, scope, omissions)

    def finalize(*args, **kwargs):
        with patch.object(LinkPlanningSession, "candidates", candidates):
            return original_finalize(*args, **kwargs)

    target, name, replacement = (
        (moc_placement_projection, "finalize", finalize)
        if finalizing
        else (LinkPlanningSession, "candidates", candidates)
    )
    with patch.object(target, name, replacement):
        yield scans


def prepare(config, db, policy, scope):
    _, _, note, moc, identity = setup(config, policy, scope, True, True)
    root = moc.parent
    (root / "Unrelated.md").write_text("# Original human note\n")
    (root / "Extra").mkdir()
    (root / "Extra/Child.md").write_text("# Original child\n")
    packet = note_placement.preview(
        config, db, *scope[:2], identity, hashlib.sha256(note.read_bytes()).hexdigest()
    )
    return note, moc, packet


@pytest.mark.parametrize("change", ["metadata", "hidden_directory"])
def test_directory_only_race_completes_first_archive_apply(config, policy, article_scope, change):
    with database.connect(config.state_root) as db:
        note, moc, packet = prepare(config, db, policy, article_scope)

        def mutate(root):
            if change == "metadata":
                touch_directory(root)
            else:
                (root / ".synthetic-cloud").mkdir()
                (root / ".synthetic-cloud/ignored.md").write_text("# Hidden\n")

        with census_change(mutate) as scans, note_read_session.reuse() as session:
            result = note_placement_execution.apply(
                config, db, *article_scope[:2], packet["operation_id"]
            )
        assert result["state"] == "completed"
        assert len(scans) == 2 and session.counts["cloud_rechecks"] == 1
        assert not note.exists() and (moc.parent / packet["target_path"]).is_file()
        assert (moc.parent / "Unrelated.md").read_text() == "# Original human note\n"


@pytest.mark.parametrize(
    "change,expected_scans",
    [
        ("new_inbound", 2),
        ("new_attachment", 2),
        ("body_edit", 2),
        ("note_inode", 2),
        ("directory_inode", 2),
        ("directory_permission", 1),
        ("continuous_metadata", 2),
    ],
)
def test_reread_never_adopts_new_sources_edits_identities_or_permissions(
    config, policy, article_scope, change, expected_scans
):
    with database.connect(config.state_root) as db:
        note, moc, packet = prepare(config, db, policy, article_scope)
        root = moc.parent
        target = root / packet["target_path"]
        mode = stat.S_IMODE(root.stat().st_mode)
        committed = []

        def mutate(root):
            if not committed:
                committed.append((target.read_bytes(), target.stat().st_ino, moc.read_bytes()))
            if change == "new_inbound":
                (root / "Fresh.md").write_text(f"# Fresh\n[[{note.name}]]\n")
            elif change == "new_attachment":
                (root / "arrived.txt").write_text("Human attachment\n")
            elif change == "body_edit":
                (root / "Unrelated.md").write_text("# Human edit during census\n")
            elif change == "note_inode":
                replacement = root / ".replacement.md"
                replacement.write_bytes((root / "Unrelated.md").read_bytes())
                replacement.replace(root / "Unrelated.md")
            elif change == "directory_inode":
                (root / "Extra").rename(root / ".Extra-preserved")
                (root / "Extra").mkdir()
                (root / "Extra/Child.md").write_bytes(
                    (root / ".Extra-preserved/Child.md").read_bytes()
                )
            elif change == "directory_permission":
                root.chmod(mode & ~stat.S_IRUSR)
            touch_directory(root)

        try:
            with (
                census_change(mutate, repeatedly=change == "continuous_metadata") as scans,
                note_read_session.reuse() as session,
                pytest.raises(IntegrityError, match="placement_link_sources_changed") as caught,
            ):
                note_placement_execution.apply(
                    config, db, *article_scope[:2], packet["operation_id"]
                )
        finally:
            root.chmod(mode)
        assert len(scans) == expected_scans
        assert session.counts.get("cloud_rechecks", 0) == expected_scans - 1
        assert not note.exists()
        assert committed == [(target.read_bytes(), target.stat().st_ino, moc.read_bytes())]
        assert (
            db.execute(
                "SELECT state FROM vault_operations WHERE id=?", (packet["operation_id"],)
            ).fetchone()[0]
            == "reconcile"
        )
        if change in {"directory_inode", "directory_permission", "continuous_metadata"}:
            assert isinstance(caught.value, cloud_observation.ObservationError)
            detail = caught.value.detail
            assert detail["path"] == ("Extra" if change == "directory_inode" else ".")
            assert detail["phase"] == "archive_link_census"
            wanted = {
                "directory_inode": "inode",
                "directory_permission": "mode",
                "continuous_metadata": "mtime_ns",
            }[change]
            assert wanted in detail["changed_fields"]
            assert detail["retryable_read"] == (change == "continuous_metadata")
        if change == "new_inbound":
            assert (root / "Fresh.md").read_text() == f"# Fresh\n[[{note.name}]]\n"
        elif change == "new_attachment":
            assert (root / "arrived.txt").read_text() == "Human attachment\n"
        elif change == "body_edit":
            assert (root / "Unrelated.md").read_text() == "# Human edit during census\n"
        elif change == "directory_inode":
            assert (root / ".Extra-preserved/Child.md").read_text() == "# Original child\n"
        if change in {"new_inbound", "new_attachment"}:
            assert caught.value.detail["reason"] == "link_paths_changed"
            assert caught.value.detail["phase"] == "archive_link_census"
            assert caught.value.detail["path"] is None
            assert caught.value.detail["changed_fields"] == ["paths"]
            assert not caught.value.detail["retryable_read"]


def test_old_committed_journal_reconciles_without_repeating_writes(config, policy, article_scope):
    with database.connect(config.state_root) as db:
        note, moc, packet = prepare(config, db, policy, article_scope)
        operation = packet["operation_id"]
        with patch.object(moc_file_journal, "record_applied", side_effect=OSError("interrupted")):
            with pytest.raises(OSError, match="interrupted"):
                note_placement_execution.apply(config, db, *article_scope[:2], operation)
        value = json.loads(
            db.execute("SELECT payload FROM vault_operations WHERE id=?", (operation,)).fetchone()[
                0
            ]
        )
        assert value["move_applied"] and value["metadata_committed"]
        # Older committed journals have no exact location records. Their original
        # path/signature/registration proof remains unchanged through reconciliation.
        value.pop("committed_locations", None)
        db.execute(
            "UPDATE vault_operations SET payload=? WHERE id=?", (json.dumps(value), operation)
        )
        target = moc.parent / packet["target_path"]
        before = target.read_bytes(), target.stat().st_ino, moc.read_bytes(), moc.stat().st_ino
        with (
            census_change(touch_directory, finalizing=False) as scans,
            patch.object(
                note_placement_execution,
                "apply_writes",
                wraps=note_placement_execution.apply_writes,
            ) as writes,
        ):
            result = note_placement_execution.apply(
                config, db, *article_scope[:2], operation, reconcile=True
            )
        assert result["state"] == "completed" and len(scans) == 2
        assert writes.call_count == 0 and not note.exists()
        assert before == (
            target.read_bytes(),
            target.stat().st_ino,
            moc.read_bytes(),
            moc.stat().st_ino,
        )
        after = json.loads(
            db.execute("SELECT payload FROM vault_operations WHERE id=?", (operation,)).fetchone()[
                0
            ]
        )
        assert after["link_observation"] == value["link_observation"]
        assert "committed_locations" not in after


def test_unchanged_link_reads_reuse_one_census_and_invalidation_discards_it(tmp_path):
    (tmp_path / "Nested").mkdir()
    (tmp_path / "Nested/Note.md").write_text("# Note\n")
    with note_read_session.reuse() as session:
        first = session.links.candidates(tmp_path, vault_scope.Scope(), {})
        assert session.links.candidates(tmp_path, vault_scope.Scope(), {}) == first
        assert session.counts["link_namespace_scans"] == 1
        assert session.counts["link_namespace_reused"] == 1
        assert session.counts.get("link_directory_scans", 0) == 0
        assert not session.counts.get("cloud_rechecks", 0)
        assert session.links.namespace is not None and session.links.children
        session.invalidate()
        assert session.links.namespace is None and not session.links.children
        assert not session.directories
        assert session.links.candidates(tmp_path, vault_scope.Scope(), {}) == first
        assert session.counts["link_namespace_scans"] == 2


def test_cached_directory_race_retries_with_a_full_fresh_census(tmp_path):
    (tmp_path / "Note.md").write_text("# Original\n")
    scope = vault_scope.Scope()
    value = {
        "link_observation": {
            "paths": ["Note.md"],
            "notes": {"Note.md": list(note_read_session.signature(tmp_path / "Note.md"))},
            "directories": {".": list(note_read_session.signature(tmp_path))},
        },
        "source_root": "Note.md",
        "target_root": "Archive/Note.md",
        "moves": {},
        "updates": [],
        "exchanges": {},
    }
    children = archive_link_census._children
    with note_read_session.reuse() as session:
        session.links.candidates(tmp_path, scope, {})
        touch_directory(tmp_path)

        def changed_after_listing(*args):
            result = children(*args)
            touch_directory(tmp_path)
            return result

        with patch.object(archive_link_census, "_children", changed_after_listing):
            archive_link_planning.verify(tmp_path, scope, value)
        assert session.counts["cloud_rechecks"] == 1
        assert session.counts["link_directory_scans"] == 1
        assert session.counts["link_namespace_scans"] == 2


def test_pre_move_directory_proof_conflict_reports_its_path_and_phase(tmp_path):
    (tmp_path / "Note.md").write_text("# Original\n")
    nested = tmp_path / "Nested"
    nested.mkdir(mode=0o755)
    value = {
        "link_observation": {
            "paths": ["Note.md"],
            "notes": {"Note.md": list(note_read_session.signature(tmp_path / "Note.md"))},
            "directories": {"Nested": list(note_read_session.signature(nested))},
        },
        "source_root": "Note.md",
        "target_root": "Archive/Note.md",
        "moves": {},
        "updates": [],
        "exchanges": {},
    }
    nested.chmod(0o700)
    with (
        note_read_session.reuse() as session,
        pytest.raises(cloud_observation.ObservationError) as caught,
    ):
        archive_link_planning.verify(tmp_path, vault_scope.Scope(), value)
    assert caught.value.detail["path"] == "Nested"
    assert caught.value.detail["phase"] == "archive_link_verify"
    assert "mode" in caught.value.detail["changed_fields"]
    assert not caught.value.detail["retryable_read"]
    assert not session.counts.get("cloud_rechecks", 0)


@pytest.mark.parametrize("explicit", [False, True])
def test_preflight_directory_tail_race_retries_only_fresh_census(tmp_path, explicit):
    from test_archive_link_planning import inventory

    (tmp_path / "Note.md").write_text("# Original\n")
    notes = inventory(tmp_path)
    planning = LinkPlanningSession() if explicit else None
    with (
        note_read_session.reuse() as active,
        census_change(touch_directory, finalizing=False) as scans,
    ):
        updates, missing = note_placement.archive_preflight(
            tmp_path, vault_scope.Scope(), notes, {"Note.md": "Archive/Note.md"}, session=planning
        )
    assert not updates and not missing
    assert len(scans) == 2 and active.counts["cloud_rechecks"] == 1


@pytest.mark.parametrize("budget", ["absent", "spent", "continuous"])
def test_preflight_never_creates_or_replenishes_a_retry_budget(tmp_path, budget):
    from contextlib import nullcontext

    from test_archive_link_planning import inventory

    (tmp_path / "Note.md").write_text("# Original\n")
    notes = inventory(tmp_path)
    context = nullcontext(None) if budget == "absent" else note_read_session.reuse()
    with context as active:
        if budget == "spent":
            active.counts["cloud_rechecks"] = 1
        with (
            census_change(touch_directory, finalizing=False, repeatedly=True) as scans,
            pytest.raises(cloud_observation.ObservationError) as caught,
        ):
            note_placement.archive_preflight(
                tmp_path, vault_scope.Scope(), notes, {"Note.md": "Archive/Note.md"}
            )
    assert len(scans) == (2 if budget == "continuous" else 1)
    assert caught.value.detail["phase"] == "archive_link_census"
    assert caught.value.detail["path"] == "."
    if active is not None:
        assert active.counts["cloud_rechecks"] == 1


def test_explicit_planning_cache_is_discarded_before_fresh_census(tmp_path):
    from test_archive_link_planning import inventory

    (tmp_path / "Note.md").write_text("# Original\n")
    notes = inventory(tmp_path)
    planning = LinkPlanningSession()
    planning.sources(notes, {"Note.md": "Archive/Note.md"})
    planning.candidates(tmp_path, vault_scope.Scope(), {})
    touch_directory(tmp_path)
    original_children = archive_link_census._children
    caches = []
    original_entries = vault_scope.metadata_entries

    def changed_children(*args):
        result = original_children(*args)
        touch_directory(tmp_path)
        return result

    def fresh_entries(*args, **kwargs):
        caches.append((planning.namespace, dict(planning.children), dict(planning.parsed)))
        yield from original_entries(*args, **kwargs)

    with (
        note_read_session.reuse() as active,
        patch.object(archive_link_census, "_children", changed_children),
        patch.object(vault_scope, "metadata_entries", fresh_entries),
    ):
        note_placement.archive_preflight(
            tmp_path, vault_scope.Scope(), notes, {"Note.md": "Archive/Note.md"}, session=planning
        )
    assert caches == [(None, {}, {})]
    assert active.counts["cloud_rechecks"] == 1


def test_incomplete_iterator_census_cannot_adopt_a_new_path_as_its_baseline(tmp_path):
    from test_archive_link_planning import inventory

    (tmp_path / "Note.md").write_text("# Original\n")
    notes = inventory(tmp_path)
    original_names = note_read_session.ReadSession.names
    changes = []

    def names(session, path, maximum):
        for name in original_names(session, path, maximum):
            yield name
            if not changes:
                changes.append(path)
                (path / "New.png").write_bytes(b"new attachment")
                touch_directory(path)

    with (
        note_read_session.reuse(),
        patch.object(note_read_session.ReadSession, "names", names),
        pytest.raises(cloud_observation.ObservationError, match="placement_link_sources_changed"),
    ):
        note_placement.archive_preflight(
            tmp_path, vault_scope.Scope(), notes, {"Note.md": "Archive/Note.md"}
        )


@pytest.mark.parametrize("addition", [False, True])
def test_original_journal_still_owns_incomplete_census_retry(tmp_path, addition):
    (tmp_path / "Note.md").write_text("# Original\n")
    value = {
        "link_observation": {
            "paths": ["Note.md"],
            "notes": {"Note.md": list(note_read_session.signature(tmp_path / "Note.md"))},
            "directories": {".": list(note_read_session.signature(tmp_path))},
        },
        "source_root": "Note.md", "target_root": "Archive/Note.md",
        "moves": {}, "updates": [], "exchanges": {},
    }
    original_names = note_read_session.ReadSession.names
    changes = []

    def names(session, path, maximum):
        for name in original_names(session, path, maximum):
            yield name
            if not changes:
                changes.append(path)
                if addition:
                    (path / "New.png").write_bytes(b"new attachment")
                touch_directory(path)

    from contextlib import nullcontext

    outcome = pytest.raises(IntegrityError) if addition else nullcontext()
    with (
        note_read_session.reuse() as active,
        patch.object(note_read_session.ReadSession, "names", names),
        outcome,
    ):
        archive_link_planning.verify(tmp_path, vault_scope.Scope(), value)
    assert active.counts["cloud_rechecks"] == 1


def test_refreshed_directory_proof_reuses_exact_observation_and_expires_on_new_change(tmp_path):
    from dataclasses import replace

    from test_archive_link_planning import inventory

    (tmp_path / "Note.md").write_text("# Original\n")
    notes = replace(
        inventory(tmp_path), directory_observations={".": note_read_session.signature(tmp_path)}
    )
    moves = {"Note.md": "Archive/Note.md"}
    with note_read_session.reuse() as active:
        with census_change(touch_directory, finalizing=False):
            note_placement.archive_preflight(tmp_path, vault_scope.Scope(), notes, moves)
        assert active.links.namespace[3] is not None
        assert note_placement.archive_preflight(tmp_path, vault_scope.Scope(), notes, moves) == (
            [], [],
        )
        touch_directory(tmp_path)
        with pytest.raises(IntegrityError, match="placement_note_changed"):
            note_placement.archive_preflight(tmp_path, vault_scope.Scope(), notes, moves)
        assert active.counts["cloud_rechecks"] == 1
        active.invalidate()
        assert active.links.namespace is None
