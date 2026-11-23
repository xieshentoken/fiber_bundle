"""Metadata-name reuse preserves every file version and directory access failure."""

import os
from unittest.mock import patch

import pytest

from fiber_bundle import storage_version_reads as reads
from fiber_bundle.storage_versions import stamp


def files(config):
    directory = config.state_root / "moc-files" / "synthetic-plan"
    directory.mkdir(parents=True)
    path = directory / "candidate.md"
    path.write_text("original metadata evidence")
    return directory, path


@pytest.mark.parametrize("change", ["new", "remove", "edit", "replace", "directory"])
def test_cached_names_never_hide_actual_metadata_changes(config, change):
    directory, path = files(config)
    original = os.scandir
    with reads.reuse(), patch.object(os, "scandir", wraps=original) as scanned:
        before = stamp(config, "synthetic-vault")
        count = scanned.call_count
        assert stamp(config, "synthetic-vault") == before
        assert scanned.call_count == count
        if change == "new":
            (directory / "outcome.json").write_text("new durable outcome")
        elif change == "remove":
            path.unlink()
        elif change == "edit":
            path.write_text("changed evidence")
        elif change == "replace":
            temporary = directory / "temporary"
            temporary.write_text(path.read_text())
            temporary.replace(path)
        else:
            displaced = directory.with_name("displaced")
            directory.rename(displaced)
            directory.mkdir()
            (directory / path.name).write_text(path.read_text() if path.exists() else "replacement")
        assert stamp(config, "synthetic-vault") != before


def test_permission_change_forces_new_enumeration_and_preserves_failure(config):
    directory, _ = files(config)
    original = os.scandir
    with reads.reuse():
        stamp(config, "synthetic-vault")
        directory.chmod(0)
        try:

            def denied(path):
                if path == directory:
                    raise PermissionError("synthetic denial")
                return original(path)

            with patch.object(os, "scandir", side_effect=denied):
                with pytest.raises(PermissionError, match="synthetic denial"):
                    stamp(config, "synthetic-vault")
        finally:
            directory.chmod(0o700)


def test_names_do_not_survive_an_independent_unit(config):
    files(config)
    original = os.scandir
    with patch.object(os, "scandir", wraps=original) as scanned:
        for _ in range(2):
            with reads.reuse():
                stamp(config, "synthetic-vault")
        assert scanned.call_count == 6


def test_metadata_file_links_keep_prior_stat_semantics_without_following_directory_links(config):
    directory, _ = files(config)
    outside = config.project_root / "outside-metadata"
    outside.mkdir()
    target = outside / "settings.json"
    target.write_text("synthetic version")
    link = directory / "file-link.json"
    link.symlink_to(target)
    (directory / "directory-link").symlink_to(outside, target_is_directory=True)
    (directory / "broken-link").symlink_to(outside / "missing")
    with reads.reuse():
        observed = stamp(config, "synthetic-vault")
        assert str(link) in observed
        assert not any("directory-link/" in path or "broken-link" in path for path in observed)
        target.write_text("edited synthetic version")
        assert stamp(config, "synthetic-vault")[str(link)] != observed[str(link)]


def test_full_cache_streams_new_directories_without_eager_allocation(config):
    directory, _ = files(config)
    cache = reads.DirectoryNames()
    cache.count = 100000
    consumed = []

    class Entry:
        name = "synthetic"

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            for number in range(1000):
                consumed.append(number)
                yield Entry()

    with patch.object(os, "scandir", return_value=Entries()):
        names = cache.names(directory)
        assert next(names) == "synthetic"
        assert consumed == [0]  # The rest is not allocated or enumerated up front.
        assert len(list(names)) == 999
    assert not cache.saved and cache.count == 100000


def test_top_level_metadata_directory_link_keeps_target_name_changes_visible(config):
    config.state_root.mkdir()
    target = config.project_root / "external-configs"
    target.mkdir()
    (target / "one.json").write_text("initial")
    (config.state_root / "configs").symlink_to(target, target_is_directory=True)
    with reads.reuse():
        before = stamp(config, "synthetic-vault")
        (target / "two.json").write_text("added without changing the symlink inode")
        after = stamp(config, "synthetic-vault")
    assert after != before
    assert str(config.state_root / "configs/two.json") in after
