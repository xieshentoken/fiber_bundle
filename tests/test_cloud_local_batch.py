"""Pinned batch parents retain fresh child/ancestor checks and never read content."""

import os
import stat
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fiber_bundle import cloud_files, cloud_local_batch, cloud_observation
from fiber_bundle.cloud_observation import ObservationError
from fiber_bundle.config import IntegrityError


def test_batch_checks_every_child_with_one_parent_ancestor_pair(tmp_path):
    for number in range(100):
        (tmp_path / f"{number}.md").write_text("unread source")
    original = os.stat
    children = []

    def observed(path, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            children.append(path)
        return original(path, *args, **kwargs)

    with patch.object(cloud_files, "require_local", wraps=cloud_files.require_local) as parents:
        with patch.object(os, "stat", side_effect=observed):
            cloud_local_batch.check(tmp_path, tuple(f"{number}.md" for number in range(100)))
        assert parents.call_count == 2
    assert len(children) == 100


@pytest.mark.parametrize("kind", ["symlink", "icloud", "dataless"])
def test_unavailable_child_is_rejected_with_its_actual_path(tmp_path, kind):
    name = "child.icloud" if kind == "icloud" else "child.md"
    path = tmp_path / name
    path.write_text("unread source")
    if kind == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "target.md")
    original = os.stat

    def observed(path, *args, **kwargs):
        if kind == "dataless" and kwargs.get("dir_fd") is not None:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_flags=cloud_files.SF_DATALESS)
        return original(path, *args, **kwargs)

    with patch.object(os, "stat", side_effect=observed):
        if kind == "symlink":
            with pytest.raises(IntegrityError, match="symlink"):
                cloud_local_batch.check(tmp_path, (name,))
        else:
            with cloud_observation.scope(tmp_path), pytest.raises(ObservationError) as failure:
                cloud_local_batch.check(tmp_path, (name,))
            assert failure.value.detail["path"] == name


@pytest.mark.parametrize("ancestor", [False, True])
def test_replaced_parent_or_symlinked_ancestor_is_rejected_after_child_stats(tmp_path, ancestor):
    base = tmp_path / "ancestor"
    parent = base / "parent"
    parent.mkdir(parents=True)
    (parent / "child.md").write_text("unread source")
    original = os.stat
    replaced = False

    def changed(path, *args, **kwargs):
        nonlocal replaced
        result = original(path, *args, **kwargs)
        if kwargs.get("dir_fd") is not None and not replaced:
            replaced = True
            if ancestor:
                displaced = tmp_path / "displaced"
                base.rename(displaced)
                base.symlink_to(displaced, target_is_directory=True)
            else:
                parent.rename(base / "displaced")
                parent.mkdir()
        return result

    with cloud_observation.scope(tmp_path), patch.object(os, "stat", side_effect=changed):
        with pytest.raises(ObservationError) as failure:
            cloud_local_batch.check(parent, ("child.md",))
        assert failure.value.detail["path"] == "ancestor/parent"
        assert failure.value.detail["changed_fields"]
        assert failure.value.detail["reason"] == (
            "scope_changed" if ancestor else "directory_changed"
        )


def test_child_replacement_and_descriptor_failure_are_never_cached_or_bypassed(tmp_path):
    path = tmp_path / "child.md"
    path.write_text("unread source")
    cloud_local_batch.check(tmp_path, (path.name,))
    path.unlink()
    path.symlink_to(tmp_path / "different.md")
    with pytest.raises(IntegrityError, match="symlink"):
        cloud_local_batch.check(tmp_path, (path.name,))
    with patch.object(os, "open", side_effect=PermissionError("synthetic denial")):
        with pytest.raises(PermissionError, match="synthetic denial"):
            cloud_local_batch.check(tmp_path, (path.name,))


@pytest.mark.parametrize("advertised", [False, True])
def test_unsupported_descriptors_keep_original_fresh_per_child_checks(tmp_path, advertised):
    for number in range(3):
        (tmp_path / f"{number}.md").write_text("unread source")
    original = cloud_files.require_local
    checked = []

    def local(path):
        checked.append(path)
        original(path)

    with (
        patch.object(cloud_local_batch, "_FD_AVAILABLE", advertised),
        patch.object(os, "open", side_effect=NotImplementedError("unsupported directory fd")),
        patch.object(cloud_files, "require_local", side_effect=local),
    ):
        cloud_local_batch.check(tmp_path, ("0.md", "1.md", "2.md"))
    assert all(tmp_path / f"{number}.md" in checked for number in range(3))
