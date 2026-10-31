import json
import time
from contextlib import nullcontext
from unittest.mock import patch

import pytest
from test_moc_files import apply, plan
from test_moc_files import file_scope as file_scope
from test_moc_files import moc_scope as moc_scope
from test_note_changes import scope as scope
from test_note_identities import identity_scope as identity_scope

from fiber_bundle import note_index, note_read_session


@pytest.mark.parametrize("reuse", [False, True])
def test_isolated_large_vault_moc_reads(config, file_scope, reuse):
    root = file_scope[2]
    folder = root / "Resource"
    folder.mkdir()
    for index in range(700):
        (folder / f"sample-{index}.md").write_text(
            f"# Sample {index}\n" + "Isolated material.\n" * 200
        )
    session = note_read_session.reuse() if reuse else nullcontext()
    started = time.monotonic()
    with (
        patch.object(note_index, "_read_note", wraps=note_index._read_note) as bodies,
        session as reads,
    ):
        planned = plan(config, file_scope)
        result = apply(config, file_scope, planned)
    assert result["moc_file_write_performed"]
    assert "<!-- fiber-moc:" in (root / "00-Maps/topic MOC.md").read_text()
    report = {
        "reuse": reuse,
        "body_reads": bodies.call_count,
        "seconds": round(time.monotonic() - started, 3),
        "counters": reads.counts if reads else None,
    }
    print("PERFORMANCE", json.dumps(report))
    if reuse:
        assert bodies.call_count <= 705
        assert reads.counts["body_reused"] >= 2800
        assert reads.counts["directory_scans"] <= 6
    else:
        assert bodies.call_count >= 3500
