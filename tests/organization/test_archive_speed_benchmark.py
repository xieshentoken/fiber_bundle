"""Opt-in cold-process synthetic archive/undo measurement, not a timing assertion."""

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

from fiber_bundle import database, note_index, publication_file_io, publication_links
from fiber_bundle.objects import digest
from fiber_bundle.organization import execution, maintenance, store
from fiber_bundle.organization.execution_reads import ExecutionReads


@pytest.mark.skipif(not os.environ.get("FIBER_ARCHIVE_BENCHMARK"), reason="explicit benchmark only")
def test_cold_archive_and_undo(config, ready):
    cid, vid, root, _, _ = ready
    import fiber_bundle.note_placement
    from fiber_bundle import note_placement_execution
    from fiber_bundle.organization import maintenance_archive

    source_modules = (
        note_index,
        fiber_bundle.note_placement,
        note_placement_execution,
        maintenance_archive,
    )
    code_sha256 = {
        module.__name__: digest(Path(module.__file__).read_bytes())
        for module in source_modules
    }
    source_path = str(fiber_bundle.note_placement.__file__)
    expected_source = os.environ.get("FIBER_BENCHMARK_EXPECT_SOURCE")
    if expected_source:
        assert source_path.startswith(expected_source), source_path
    for number in range(8):
        (root / f"Inbox/热传导-{number}.md").write_text(
            f"# 热传导-{number}\n#热管理\n合成归档笔记。\n"
        )
    body = "无关资料与人工记录保留。" * 700
    for number in range(180):
        (root / f"Resource/existing-{number}.md").write_text(f"# Existing {number}\n{body}\n")
    assets = int(os.environ.get("FIBER_ARCHIVE_ASSETS", "0"))
    if assets:
        directory = root / "Resource/assets"
        directory.mkdir()
        for number in range(assets):
            (directory / f"attachment-{number}.bin").write_bytes(b"synthetic attachment")
    incoming = root / "Resource/incoming.md"
    incoming.write_text("# 合成引用\n" + "\n".join(f"[[Inbox/热传导-{i}]]" for i in range(8)))
    from fiber_bundle import note_identity_store

    root_info = root.stat()
    for path in sorted((root / "Inbox").glob("*.md")):
        relative = path.relative_to(root).as_posix()
        current = note_index.read_note(root, relative, 10000)
        record = note_identity_store.Registration(
            "note:" + str(uuid5(NAMESPACE_URL, "synthetic-benchmark:" + relative)),
            None,
            relative,
            current.sha256,
            current.file_key,
            (root_info.st_dev, root_info.st_ino),
            1,
        )
        note_identity_store.save(config, vid, cid, record, expected_revision=None)
    before = {
        p.relative_to(root).as_posix(): digest(p.read_bytes())
        for p in root.rglob("*")
        if p.is_file()
    }
    preview_started = time.monotonic()
    with database.locked(config), database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        preview_seconds = time.monotonic() - preview_started
        confirm_started = time.monotonic()
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        confirm_seconds = time.monotonic() - confirm_started
    counters = {
        "note_body_reads": 0,
        "publication_body_reads": 0,
        "directory_reads": 0,
        "link_body_scans": 0,
        "link_scan_bytes": 0,
    }
    original_note, original_publication, original_scan = (
        note_index._read_note,
        publication_file_io.read,
        os.scandir,
    )

    def note(*a, **k):
        counters["note_body_reads"] += 1
        return original_note(*a, **k)

    def publication(*a, **k):
        counters["publication_body_reads"] += 1
        return original_publication(*a, **k)

    def scan(*a, **k):
        counters["directory_reads"] += 1
        return original_scan(*a, **k)

    original_reference, original_prefilter = (
        publication_links.references,
        publication_links.may_reference_moved,
    )

    def references(text):
        counters["link_body_scans"] += 1
        counters["link_scan_bytes"] += len(text.encode())
        return original_reference(text)

    def prefilter(text, *args, **kwargs):
        counters["link_body_scans"] += 1
        counters["link_scan_bytes"] += len(text.encode())
        return original_prefilter(text, *args, **kwargs)

    reads = ExecutionReads()
    started = time.monotonic()
    with (
        patch.object(note_index, "_read_note", note),
        patch.object(publication_file_io, "read", publication),
        patch.object(os, "scandir", scan),
        patch.object(publication_links, "references", references),
        patch.object(publication_links, "may_reference_moved", prefilter),
    ):
        for _ in range(20):
            execution.run(config, job["id"], reads=reads)
            job = store.get(config, "jobs", job["id"])
            if job["state"] not in {"queued", "running"}:
                break
    elapsed = time.monotonic() - started
    assert job["state"] == "completed", job.get("error")
    assert job["completed"] == 9

    def normalized(raw):
        return re.sub(
            rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            b"<synthetic-uuid>",
            raw,
        )

    archived = {
        p.relative_to(root).as_posix(): digest(normalized(p.read_bytes()))
        for p in root.rglob("*")
        if p.is_file() and not p.name.startswith(".fiber-")
    }
    archived_texts = {
        p.relative_to(root).as_posix(): normalized(p.read_bytes()).decode("utf-8", "replace")
        for p in root.rglob("*.md")
        if "existing-" not in p.name
    }
    assert all((root / f"Resource/热传导-{i}.md").is_file() for i in range(8))
    assert "[[Resource/热传导-0]]" in incoming.read_text()
    with database.locked(config), database.connect(config.state_root) as db:
        maintenance.action(config, db, job, "cancel", None)
    undo_started = time.monotonic()
    for _ in range(30):
        execution.run(config, job["id"])
        undone = store.get(config, "jobs", job["id"])
        if undone["state"] != "cancelling":
            break
    assert undone["state"] == "cancelled", undone.get("error")
    after = {
        p.relative_to(root).as_posix(): digest(p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and not p.name.startswith(".fiber-")
    }
    assert after == before
    result = {
        "loaded_source": source_path,
        "code_sha256": code_sha256,
        "preview_seconds": preview_seconds,
        "confirm_seconds": confirm_seconds,
        "total_seconds": preview_seconds + confirm_seconds + elapsed,
        "archive_seconds": elapsed,
        "undo_seconds": time.monotonic() - undo_started,
        "counts_scope": "application only; index completion is separate",
        "counts": counters,
        "read_work": job.get("read_work"),
        "phase_timings": job.get("timings"),
        "archived": 9,
        "files": len(before),
        "attachments": assets,
        "before_digest": digest(json.dumps(before, sort_keys=True).encode()),
        "archived_digest": digest(json.dumps(archived, sort_keys=True).encode()),
        "archived_files": archived,
        "changed_texts": archived_texts,
        "undo_exact": after == before,
        "model_called": job["model_called"],
    }
    assert code_sha256 == {
        module.__name__: digest(Path(module.__file__).read_bytes()) for module in source_modules
    }
    Path(os.environ["FIBER_ARCHIVE_BENCHMARK"]).write_text(json.dumps(result, indent=2))
