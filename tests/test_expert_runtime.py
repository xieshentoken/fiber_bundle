"""Full compiler identity, scope, and real offline pause/Draft compilation."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner
from test_source_imports import request, upload
from test_source_processing import WORKERS

from fiber_bundle import (
    database,
    expert_execution,
    expert_runtime,
    expert_tasks,
    source_processing_policy,
)
from fiber_bundle import source_import_execution as execution
from fiber_bundle.config import InvalidInput


def configured(config, runtime):
    from fiber_bundle.source_processing_contract import normalize

    return source_processing_policy.save(
        config,
        None,
        {
            **normalize(WORKERS),
            **WORKERS,
            "schema": 4,
            "revision": 1,
            "ocr_runtime": None,
            "conversion_runtime": None,
            "expert_runtime": runtime,
        },
        None,
    )


def test_full_binding_never_claims_independent_review():
    value = expert_runtime.receipt("sha256:" + "a" * 64, "b" * 64, {"passed": True})
    expert_runtime.verify(value)
    for key, replacement in [
        ("release", "other"),
        ("knowledge_verified", True),
        ("image", "mutable:tag"),
    ]:
        with pytest.raises(InvalidInput):
            expert_runtime.verify({**value, key: replacement})
    with pytest.raises(InvalidInput):
        expert_runtime.selection("scan-text", None)


@pytest.mark.skipif(
    not os.getenv("FIBER_EXPERT_RUNTIME"), reason="explicit full compiler runtime required"
)
@pytest.mark.parametrize("packed", [False, True])
def test_real_full_compiler_pause_or_structured_draft(config, store, owner, packed, tmp_path):
    cid, _, ids = owner
    root = Path(os.environ["FIBER_EXPERT_FIXTURES"])
    runtime = json.loads(Path(os.environ["FIBER_EXPERT_RUNTIME"]).read_bytes())
    settings = configured(config, runtime)
    db = database.connect(config.state_root)
    workpack_id = None
    if packed:
        raw = (root / "workpack.zip").read_bytes()
        workpack_id = upload(
            config,
            db,
            request(
                cid, ids["personal"], raw, "workpack.zip", sha=settings["configuration_sha256"]
            ),
            raw,
        )["id"]
    raw = (root / "candidate.pdf").read_bytes()
    req = request(
        cid, ids["personal"], raw, "candidate.pdf", "process", settings["configuration_sha256"]
    )
    req.update(
        engine="fake-expert",
        expert_profile="native-text",
        expert_backend=None,
        workpack_import_id=workpack_id,
    )
    imported = upload(config, db, req, raw)
    identity = execution.prepare(config, db, cid, imported["id"])
    assert identity == imported["id"]
    db.close()
    if packed:
        from uuid import uuid4

        from test_feishu_ingress import payload

        from fiber_bundle import expert_feishu, feishu_receiver
        from fiber_bundle.weixin_events import Binding

        binding = Binding("feishu:cli_synthetic", "ou_owner", cid, ids["personal"])
        feishu_receiver.initialize(config, binding)
        authorization = str(uuid4())
        event = payload(body={"text": f"/fake-expert start {identity}"})
        feishu_receiver.capture(
            config, binding, event, expert_authorization=authorization, expert_limit=1
        )
        with patch("fiber_bundle.expert_dispatch.subprocess.Popen") as launch:
            launch.return_value.wait.return_value = 0
            submitted = expert_feishu.dispatch_one(config, binding, authorization)
            assert submitted["id"] == identity and launch.call_count == 1
        assert expert_feishu.dispatch_one(config, binding, authorization) is None
    db.close()
    expert_execution.execute(config, identity)
    db = database.connect(config.state_root)
    task = expert_tasks.get(db, identity)
    assert task["state"] == ("completed" if packed else "paused")
    result = json.loads(task["result_json"])
    assert result["code"] == ("draft_compiled" if packed else "semantic_authoring_required")
    if packed:
        from fiber_bundle import expert_evidence, expert_publication, note_search_build, search

        packet = expert_evidence.describe(config, db, cid, identity)
        artifact_sha = packet["manifest_sha256"]
        with pytest.raises(InvalidInput):
            expert_publication.publish(config, db, cid, identity, artifact_sha, False, True)
        from fiber_bundle.expert_reference import query

        db.close()
        reference = query(config, cid, identity, "Candidate power 12 watts")
        assert reference["matched_object_ids"]
        assert reference["assurance"]["review_state"] == "deferred"
        assert reference["assurance"]["knowledge_verified"] is False
        guarded = query(config, cid, identity, "certify safe design decision")
        assert guarded["behavior"] == "abstain"
        db = database.connect(config.state_root)
        published = expert_publication.publish(config, db, cid, identity, artifact_sha, True, True)
        assert published["review_state"] == "deferred"
        assert expert_publication.publish(config, db, cid, identity, artifact_sha, True, True)[
            "historical"
        ]
        db.close()
        indexed = note_search_build.build(config, cid, ids["personal"])
        assert (
            indexed["notes"] == 2
        )  # Canonical Skill Markdown is preserved, excluded from evidence search.
        db = database.connect(config.state_root)
        hits = search.search(config, db, cid, "12 watts", [ids["personal"]])["results"]
        assert hits and hits[0]["source_kind"] == "agent_analysis"
        assert hits[0]["independent_evidence"] is False
        from fiber_bundle import backup_bundle, backup_restore, note_search_jobs
        from fiber_bundle.auth import Denied
        from fiber_bundle.config import load_config

        note_search_jobs.record(config, ids["personal"], "building", reason="explicit_update")
        db.close()
        backup_bundle.create(config, tmp_path / "bundle", "synthetic-only-backup-password")
        restored_result = backup_restore.restore(
            tmp_path / "bundle", tmp_path / "restored", "synthetic-only-backup-password"
        )
        assert restored_result["automatic_start"] is False
        restored = load_config(tmp_path / "restored/config.test.json", tmp_path / "restored")
        backup_restore.activate(restored, source_stopped=True)
        restored_db = database.connect(restored.state_root)
        assert expert_tasks.get(restored_db, identity)["state"] == "completed"
        with pytest.raises(Denied):
            expert_evidence.describe(restored, restored_db, cid, identity)
        assert (
            restored_db.execute("SELECT state FROM source_expert_publications").fetchone()[0]
            == "published"
        )
        assert note_search_jobs.read(restored, ids["personal"])["state"] == "interrupted"
        assert restored_db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        restored_db.close()
        db = database.connect(config.state_root)
        assert result["artifact_manifest"]["object_count"] == 1
        assert result["artifact_manifest"]["anchor_count"] > 0
    else:
        with patch("fiber_bundle.expert_dispatch.subprocess.Popen") as launch:
            launch.return_value.wait.return_value = 0
            from fiber_bundle.expert_dispatch import resume

            resume(config, db, cid, identity, None)
            assert launch.call_count == 1
        db.close()
        expert_execution.execute(config, identity)
        db = database.connect(config.state_root)
        assert expert_tasks.get(db, identity)["state"] == "paused"
        assert db.execute("select count(*) from source_expert_attempts").fetchone()[0] == 2
        raw_pack = (root / "workpack.zip").read_bytes()
        pack_id = upload(
            config,
            db,
            request(
                cid, ids["personal"], raw_pack, "workpack.zip", sha=settings["configuration_sha256"]
            ),
            raw_pack,
        )["id"]
        with patch("fiber_bundle.expert_dispatch.subprocess.Popen") as launch:
            launch.return_value.wait.return_value = 0
            from fiber_bundle.expert_dispatch import resume

            resume(config, db, cid, identity, pack_id)
        db.close()
        expert_execution.execute(config, identity)
        db = database.connect(config.state_root)
        assert expert_tasks.get(db, identity)["state"] == "completed"
        assert db.execute("select count(*) from source_expert_attempts").fetchone()[0] == 3
    assert db.execute("select count(*) from model_calls").fetchone()[0] == 0
    assert db.execute("select count(*) from document_publications").fetchone()[0] == 0
    with pytest.raises(InvalidInput, match="expert_start_consumed"):
        expert_execution.execute(config, identity)
    db.close()


def test_explicit_feishu_command_scope_idempotence_and_ambiguity(config, store, owner):
    from copy import deepcopy
    from uuid import uuid4

    from test_feishu_ingress import payload

    from fiber_bundle import expert_feishu, feishu_receiver, weixin_inbox
    from fiber_bundle.weixin_events import Binding

    cid, _, ids = owner
    binding = Binding("feishu:cli_synthetic", "ou_owner", cid, ids["personal"])
    feishu_receiver.initialize(config, binding)
    authorization = str(uuid4())
    identity = str(uuid4())
    original = payload(body={"text": f"/fake-expert start {identity}"})
    kwargs = dict(expert_authorization=authorization, expert_limit=2)
    assert (
        feishu_receiver.capture(config, binding, original, **kwargs)["state"]
        == "expert_command_recorded"
    )
    assert (
        feishu_receiver.capture(config, binding, original, **kwargs)["state"]
        == "expert_command_recorded"
    )
    uncertain = payload(body={"text": "/fake-expert process recent PDF"}, identity="message-2")
    feishu_receiver.capture(config, binding, uncertain, **kwargs)
    with weixin_inbox.connection(config, binding) as db:
        states = [
            r[0] for r in db.execute("SELECT state FROM fiber_expert_commands ORDER BY rowid")
        ]
        assert states == ["queued", "needs_selection"]
    foreign = deepcopy(original)
    foreign["event"]["sender"]["sender_id"]["open_id"] = "ou_other"
    assert feishu_receiver.capture(config, binding, foreign, **kwargs)["state"] == "ignored"
    with pytest.raises(Exception) as failure:
        expert_feishu.dispatch_one(config, binding, authorization)
    from fiber_bundle.auth import Denied

    assert isinstance(failure.value, Denied)
    assert expert_feishu.dispatch_one(config, binding, authorization) is None
    with weixin_inbox.connection(config, binding) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM fiber_expert_commands WHERE state='failed'"
            ).fetchone()[0]
            == 1
        )


def test_workpack_archive_traversal_and_executable_are_rejected(tmp_path):
    import io
    import zipfile

    from fiber_bundle.document_source import DocumentFile
    from fiber_bundle.expert_workpack import unpack
    from fiber_bundle.objects import digest

    for n, name in enumerate(["../x.json", "a//x.json", "a/./x.json", "script.py", "/x.json"]):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("workpack.json", "{}")
            archive.writestr(name, "{}")
        raw = data.getvalue()
        path = tmp_path / f"{n}.zip"
        path.write_bytes(raw)
        with pytest.raises(InvalidInput):
            unpack(DocumentFile(path, digest(raw), len(raw)), tmp_path / f"out{n}")


def test_abandoned_attempt_quarantines_without_replay_but_live_owner_survives(config, store, owner):
    from uuid import uuid4

    from document_support import pdf_bytes

    from fiber_bundle.executor_container import run_lock
    from fiber_bundle.expert_recovery import reconcile

    cid, _, ids = owner
    runtime = expert_runtime.receipt("sha256:" + "a" * 64, "b" * 64, {"passed": True})
    settings = configured(config, runtime)
    raw = pdf_bytes()
    req = request(
        cid, ids["personal"], raw, "source.pdf", "process", settings["configuration_sha256"]
    )
    req["engine"] = "fake-expert"
    db = database.connect(config.state_root)
    imported = upload(config, db, req, raw)
    identity = execution.prepare(config, db, cid, imported["id"])
    db.execute(
        "UPDATE source_expert_tasks SET state='running',current_attempt=1,"
        "launch_state='launched' WHERE id=?",
        (identity,),
    )
    db.execute(
        "INSERT INTO source_expert_attempts VALUES(?,1,?,NULL,'running',NULL,1)",
        (identity, str(uuid4())),
    )
    db.close()
    with run_lock(config, identity):
        assert reconcile(config) == 0
    assert reconcile(config) == 1
    assert reconcile(config) == 0
    db = database.connect(config.state_root)
    assert expert_tasks.get(db, identity)["state"] == "interrupted"
    with patch("fiber_bundle.expert_dispatch.subprocess.Popen") as child:
        from fiber_bundle.expert_dispatch import launch

        assert launch(config, db, cid, identity)["state"] == "interrupted"
        child.assert_not_called()
    assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
    db.close()


def test_render_preserves_canonical_bytes_and_only_object_anchors():
    from fiber_bundle.expert_evidence import render
    from fiber_bundle.inputs import canonical

    first = {"id": "one", "title": "First", "evidence_ids": ["a"]}
    second = {"id": "two", "title": "Second", "evidence_ids": ["b"]}
    files = {
        "references/knowledge/index.json": canonical(
            {"objects": [{"path": "one.json"}, {"path": "two.json"}]}
        ).encode(),
        "one.json": canonical(first).encode(),
        "two.json": canonical(second).encode(),
        "references/evidence/anchors.jsonl": b'{"id":"a","pages":[1]}\n{"id":"b","pages":[2]}\n',
        "references/runtime/catalog.json": canonical({"objects": [first, second]}).encode(),
    }
    notes = render({"source_sha256": "a" * 64, "version_id": "v", "asset_id": "x"}, {}, files)
    assert b'"pages":[1]' in notes["object-0001.md"]
    assert b'"pages":[2]' not in notes["object-0001.md"]
    assert b'"pages":[2]' in notes["object-0002.md"]
    assert all(notes[".fiber-expert-canonical/" + name] == raw for name, raw in files.items())
