from unittest.mock import patch

import pytest

from fiber_bundle import note_index, note_read_session
from fiber_bundle.config import IntegrityError
from fiber_bundle.vault_scope import Scope


def test_reuses_bodies_but_rechecks_availability_and_invalidates_edits(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# Before\n")
    calls = []
    with note_read_session.reuse() as session:
        first = note_index.read_note(tmp_path, path.name, 1024, availability=calls.append)
        assert note_index.read_note(tmp_path, path.name, 1024, availability=calls.append) == first
        path.write_text("# Edited\n")
        assert (
            note_index.read_note(tmp_path, path.name, 1024, availability=calls.append).title
            == "Edited"
        )
        alternate = tmp_path / "alternate"
        alternate.write_text("# Replaced\n")
        alternate.replace(path)
        assert (
            note_index.read_note(tmp_path, path.name, 1024, availability=calls.append).title
            == "Replaced"
        )
        assert session.counts == {
            "body_reads": 3,
            "body_reused": 1,
            "directory_scans": 0,
            "directory_reused": 0,
        }
    assert len(calls) == 4
    with note_read_session.reuse() as new:
        note_index.read_note(tmp_path, path.name, 1024, availability=calls.append)
        assert new.counts["body_reads"] == 1  # Never reused across task steps/restarts.


def test_cached_body_cannot_bypass_cloud_failure_or_limit(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# Content\n")
    with note_read_session.reuse():
        note_index.read_note(tmp_path, path.name, 1024, availability=lambda _: None)

        def blocked(_):
            raise IntegrityError("cloud_metadata_unavailable")

        with pytest.raises(IntegrityError, match="cloud_metadata_unavailable"):
            note_index.read_note(tmp_path, path.name, 1024, availability=blocked)
        with pytest.raises(IntegrityError, match="bounded"):
            note_index.read_note(tmp_path, path.name, 2, availability=lambda _: None)
        path.chmod(0o640)
        with patch.object(note_index, "_read_note", wraps=note_index._read_note) as body:
            note_index.read_note(tmp_path, path.name, 1024, availability=lambda _: None)
            assert body.call_count == 1


def test_directory_membership_is_refreshed_and_exclusions_precede_file_access(tmp_path):
    (tmp_path / "allowed.md").write_text("# A")
    protected = tmp_path / "excluded"
    protected.mkdir()
    (protected / "do-not-read.md").write_text("private")
    scope = Scope(exclude=("excluded",))
    with note_read_session.reuse() as session:
        assert [
            n.relative_path for n in note_index.snapshot(tmp_path, set(), set(), scope=scope)
        ] == ["allowed.md"]
        note_index.snapshot(tmp_path, set(), set(), scope=scope)
        assert session.counts["directory_scans"] == 1
        (tmp_path / "new.md").write_text("# B")
        assert len(note_index.snapshot(tmp_path, set(), set(), scope=scope)) == 2
        (tmp_path / "allowed.md").unlink()
        assert [
            n.relative_path for n in note_index.snapshot(tmp_path, set(), set(), scope=scope)
        ] == ["new.md"]
        assert protected not in session.directories
        assert all("do-not-read" not in str(key) for key in session.notes)


def test_cached_directory_detects_changes_during_iteration(tmp_path):
    (tmp_path / "one.md").write_text("# One")
    with note_read_session.reuse() as session:
        assert list(session.names(tmp_path, 100)) == ["one.md"]
        names = session.names(tmp_path, 100)
        assert next(names) == "one.md"
        (tmp_path / "new.md").write_text("# New")
        with pytest.raises(IntegrityError, match="note directory changed"):
            list(names)
