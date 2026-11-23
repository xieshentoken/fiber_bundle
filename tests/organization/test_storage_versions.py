"""Optimistic read guards observe fresh membership and metadata after every boundary."""

import pytest

from fiber_bundle.organization import store
from fiber_bundle.storage_versions import stamp


def test_same_file_membership_and_signatures_as_path_guard(config):
    root = config.state_root
    paths = [
        "settings.json",
        "configs/one.json",
        "query-policies/one.json",
        "model-secrets/one",
        "moc-files/a/deep/candidate.md",
        "moc-note-files/b/.hidden",
        "moc-files/file-link",
    ]
    for relative in paths[:-1]:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
    (root / paths[-1]).symlink_to(root / paths[0])
    (root / "moc-files/dir-link").symlink_to(root / "query-policies", target_is_directory=True)
    (root / "configs/nested").mkdir()
    (root / "configs/nested/ignored").write_text("not a direct config")
    (root / "backend.json").write_text("independent heartbeat")
    value = stamp(config, "vault")
    # Existing state files are allowed, but the added fixture must match the old
    # Path walker exactly, including file links and hidden recovery candidates.
    expected = {str(root / p) for p in paths}
    assert expected <= value.keys()
    assert str(root / "moc-files/dir-link/one.json") not in value
    assert str(root / "configs/nested/ignored") not in value
    assert str(root / "backend.json") not in value
    for name in expected:
        info = (root / name).stat()
        assert value[name] == (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )


@pytest.mark.parametrize(
    "directory",
    ["configs", "query-policies", "model-secrets", "moc-files/a/deep", "moc-note-files/b"],
)
def test_next_boundary_detects_add_edit_replace_and_delete(config, directory):
    path = config.state_root / directory / "candidate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    empty = stamp(config, "vault")
    path.write_text("before")
    added = stamp(config, "vault")
    assert added != empty
    path.write_text("edited")  # Same length still advances metadata.
    edited = stamp(config, "vault")
    assert edited != added
    replacement = path.with_name("replacement")
    replacement.write_text("edited")
    replacement.replace(path)
    replaced = stamp(config, "vault")
    assert replaced != edited
    path.unlink()
    assert stamp(config, "vault") == empty


def test_semantic_row_changes_invalidate_but_discovery_and_other_vault_do_not(config):
    config.state_root.mkdir(parents=True)
    store.put(config, "decisions", "own", "vault", {"state": "preview"})
    before = stamp(config, "vault")
    store.put(config, "package_scans", "scan", "vault", {"state": "current"})
    store.put(config, "decisions", "other", "other-vault", {"state": "preview"})
    assert stamp(config, "vault") == before
    store.put(config, "decisions", "own", "vault", {"state": "cancelled"})
    changed = stamp(config, "vault")
    assert changed != before
    with store.connection(config, write=True) as db:
        db.execute("DELETE FROM decisions WHERE id='own'")
    assert stamp(config, "vault") != changed
