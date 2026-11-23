"""Retained verification still notices external edits and identity replacements."""

from unittest.mock import patch

import pytest

from fiber_bundle.config import IntegrityError
from fiber_bundle.objects import digest
from fiber_bundle.organization import maintenance_undo_blob_reads as reads
from fiber_bundle.organization import maintenance_undo_blobs as blobs


@pytest.mark.parametrize("replacement", [False, True])
def test_unchanged_blob_is_checked_once_but_corruption_always_blocks(config, replacement):
    config.state_root.mkdir()
    raw = "synthetic original payload"
    row = {"value": raw}
    blobs.pack(config, row, persist=True)
    path = config.state_root / "maintenance-undo-blobs" / digest(raw.encode())
    with reads.reuse(), patch.object(blobs, "read", wraps=blobs.read) as loaded:
        blobs.pack(config, row, persist=True)
        blobs.pack(config, row, persist=True)
        assert loaded.call_count == 1
        if replacement:
            temporary = path.with_suffix(".replacement")
            temporary.write_text("corrupt external replacement")
            temporary.replace(path)
        else:
            path.write_text("corrupt external edit")
        with pytest.raises(IntegrityError, match="blob_unavailable"):
            blobs.pack(config, row, persist=True)
        assert loaded.call_count == 2


def test_verification_never_crosses_unrelated_read_units(config):
    config.state_root.mkdir()
    row = {"value": "synthetic payload"}
    blobs.pack(config, row, persist=True)
    with patch.object(blobs, "read", wraps=blobs.read) as loaded:
        for _ in range(2):
            with reads.reuse():
                blobs.pack(config, row, persist=True)
                blobs.pack(config, row, persist=True)
        assert loaded.call_count == 2
