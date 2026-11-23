"""Opt-in whole-workflow measurement over identical synthetic large libraries."""

import json
import os
import re
import time
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import pytest
from test_jobs import ready as ready
from test_maintenance import preview

from fiber_bundle import (
    cloud_files,
    database,
    note_identity_store,
    note_index,
    note_search_cache,
    publication_inventory_read,
)
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest
from fiber_bundle.organization import execution, maintenance, store
from fiber_bundle.organization.execution_reads import ExecutionReads
from fiber_bundle.vault_sync import index, scanner
from fiber_bundle.vault_sync import store as sync


@pytest.mark.skipif(not os.environ.get("FIBER_RELIABILITY_BENCHMARK"), reason="explicit benchmark")
def test_whole_large_library_workflow(config, ready):
    expected = os.environ.get("FIBER_RELIABILITY_SOURCE")
    if expected:
        assert str(note_index.__file__).startswith(expected), note_index.__file__
    cid, vid, root, _, _ = ready
    for number in range(8):
        (root / f"Inbox/热传导-{number}.md").write_text(f"# 热传导 {number}\n#热管理\n合成资料。\n")
    body = "无关资料与人工记录保留。" * 700
    for number in range(726):
        (root / f"Resource/existing-{number:04}.md").write_text(f"# Existing {number}\n{body}\n")
    incoming = root / "Resource/incoming.md"
    incoming.write_text("# 合成引用\n" + "\n".join(f"[[Inbox/热传导-{i}]]" for i in range(8)))
    assets = int(os.environ.get("FIBER_RELIABILITY_ASSETS", "16000"))
    for group in range(4):
        directory = root / f"Attachments/assets-{group}"
        directory.mkdir(parents=True)
        for number in range(group, assets, 4):
            (directory / f"attachment-{number}.bin").write_bytes(b"synthetic attachment")
    root_info = root.stat()
    for path in sorted((root / "Inbox").glob("*.md")):
        relative = path.relative_to(root).as_posix()
        note = note_index.read_note(root, relative, 10000)
        record = note_identity_store.Registration(
            "note:" + str(uuid5(NAMESPACE_URL, "synthetic-benchmark:" + relative)),
            None,
            relative,
            note.sha256,
            note.file_key,
            (root_info.st_dev, root_info.st_ino),
            1,
        )
        note_identity_store.save(config, vid, cid, record, expected_revision=None)
    with database.connect(config.state_root) as db:
        for number in range(80):
            relative = f"Resource/existing-{number:04}.md"
            note = note_index.read_note(root, relative, 1024 * 1024)
            plan = {
                "layout_version": 3,
                "mapping": {"note.md": relative},
                "published_hashes": {relative: note.sha256},
                "file_keys": {relative: list(note.file_key)},
                "body_files": ["note.md"],
                "outputs": {"note.md": "synthetic-output-" * 8192},
                "policy_binding": {"settings": {}},
            }
            db.execute(
                "INSERT INTO publication_locations VALUES(?,?,?,?,?,?,?,?)",
                (
                    "intake",
                    f"benchmark-{number}",
                    vid,
                    relative,
                    None,
                    1,
                    "published",
                    canonical(plan),
                ),
            )
    counts = {
        "note_body_reads": 0,
        "directory_scans": 0,
        "publication_projection_scans": 0,
        "cloud_local_checks": 0,
    }
    originals = note_index._read_note, os.scandir, publication_inventory_read._rows
    original_local = cloud_files.require_local
    input_files = {
        path.relative_to(root).as_posix(): digest(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }

    def read(*args, **kwargs):
        counts["note_body_reads"] += 1
        return originals[0](*args, **kwargs)

    def scan(*args, **kwargs):
        counts["directory_scans"] += 1
        return originals[1](*args, **kwargs)

    def rows(*args, **kwargs):
        counts["publication_projection_scans"] += 1
        return originals[2](*args, **kwargs)

    def local(*args, **kwargs):
        counts["cloud_local_checks"] += 1
        return original_local(*args, **kwargs)

    timing, phases = {}, {}
    reads = ExecutionReads()
    started = time.monotonic()
    with (
        patch.object(note_index, "_read_note", read),
        patch.object(os, "scandir", scan),
        patch.object(publication_inventory_read, "_rows", rows),
        patch.object(cloud_files, "require_local", local),
    ):
        with database.locked(config), database.connect(config.state_root) as db:
            began = time.monotonic()
            job = preview(config, db, cid, vid, "quick")
            timing["preview_seconds"] = time.monotonic() - began
            phases["preview"] = dict(counts)
            began = time.monotonic()
            maintenance.action(config, db, job, "confirm", job["plan_sha256"])
            timing["confirmation_seconds"] = time.monotonic() - began
            phases["confirmation"] = dict(counts)
        began = time.monotonic()
        for _ in range(30):
            execution.run(config, job["id"], reads=reads)
            job = store.get(config, "jobs", job["id"])
            if job["state"] not in {"queued", "running"}:
                break
        timing["application_seconds"] = time.monotonic() - began
        phases["application"] = dict(counts)
        assert job["state"] == "completed", job.get("error")
        assert job["completed"] == 9
        began = time.monotonic()
        from fiber_bundle import auth, vault_scope

        with database.connect(config.state_root) as db:
            scope = vault_scope.for_vault(root, auth.grant(db, cid, vid, "query"))
        sync.register(config, vid, str(root), scope, "synthetic-benchmark")
        # Explicit deterministic event time; each bounded scanner slice is real.
        for _ in range(500):
            tasks = sync.due(config, now=100000000000.0)
            if not tasks:
                break
            for task in tasks:
                scanner.apply(config, task, now=10)
        assert sync.status(config, vid)["active_pending"] == 0
        timing["observation_seconds"] = time.monotonic() - began
        slices_started = time.monotonic()
        slices = []
        for _ in range(30):
            before_slice = time.monotonic()
            result = index.update(config, cid, vid)
            slices.append({"seconds": time.monotonic() - before_slice, **result})
            if not result["pending_notes"]:
                break
        timing["index_seconds"] = time.monotonic() - began
        timing["index_slices_seconds"] = time.monotonic() - slices_started
        assert result["state"] == "ready" and result["notes"] == 737, result
    timing["total_seconds"] = time.monotonic() - started

    def normalized(raw):
        return re.sub(
            rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            b"<synthetic-uuid>",
            raw,
        )

    output = {
        path.relative_to(root).as_posix(): digest(normalized(path.read_bytes()))
        for path in root.rglob("*")
        if path.is_file() and not path.name.startswith(".fiber-")
    }
    assert all((root / f"Resource/热传导-{i}.md").exists() for i in range(8))
    assert "[[Resource/热传导-0]]" in incoming.read_text()
    with note_search_cache.connection(config, vid) as index_db:
        indexed = []
        for path, raw in index_db.execute("SELECT path,metadata FROM notes ORDER BY path"):
            metadata = json.loads(raw)
            body = (root / path).read_bytes()
            assert metadata["sha256"] == digest(body)
            indexed.append([path, digest(normalized(body)), metadata["title"]])
        chunks = [
            [path, line, *[normalized(text.encode()).decode() for text in fields]]
            for path, line, *fields in index_db.execute(
                "SELECT path,line,snippet,body,title FROM chunks "
                "ORDER BY path,line,snippet,body,title"
            )
        ]
    Path(os.environ["FIBER_RELIABILITY_BENCHMARK"]).write_text(
        json.dumps(
            {
                "source": str(note_index.__file__),
                "ordinary_notes": 736,
                "mocs": 1,
                "assets": assets,
                "publication_rows": 80,
                "archived": 9,
                "timing": timing,
                "counts": counts,
                "phase_counts": phases,
                "read_work": job.get("read_work"),
                "phase_timings": job.get("timings"),
                "index_slices": slices,
                "output_sha256": digest(canonical(output).encode()),
                "input_sha256": digest(canonical(input_files).encode()),
                "index_sha256": digest(canonical(indexed).encode()),
                "chunks_sha256": digest(canonical(chunks).encode()),
                "output_files": output,
                "model_called": job["model_called"],
            },
            indent=2,
        )
    )
