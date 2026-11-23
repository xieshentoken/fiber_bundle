"""Real retained-evidence capacity failures remain observable without retrying a task."""

import json
import subprocess
import time
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_jobs import ready as ready
from test_maintenance import preview
from test_retained_moc_evidence import captured, large_missing_history

from fiber_bundle import auth, database, moc_store
from fiber_bundle.api import create_app
from fiber_bundle.cloud_observation import ObservationError
from fiber_bundle.config import IntegrityError
from fiber_bundle.organization import maintenance, read_errors, store
from fiber_bundle.organization.observation_diagnostic_http import response
from fiber_bundle.vault_sync import scanner
from fiber_bundle.vault_sync import store as sync_store


def assert_frontend_evidence(public):
    """Feed the actual HTTP diagnostic to shared DOM rendering and Atlas retranslation."""
    script = """
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
globalThis.localStorage = {getItem: () => 'zh-CN', setItem() {}};
const { setLocale } = await import('./src/fiber_bundle/web/i18n/index.js');
const { appendExecutionStatus } =
  await import('./src/fiber_bundle/web/organization_execution_status.js');
const { errorText } = await import('./src/fiber_bundle/web/atlas/feedback.js');
const { installDOM } = await import('./tests/web/reliability_dom_fixture.js');
const job = JSON.parse(readFileSync(0, 'utf8'));
const detail = job.error_detail;
const facts = [detail.path, detail.moc_id, String(detail.actual_bytes),
  String(detail.limit_bytes), ...detail.missing_members];
const dom = installDOM();
for (const locale of ['zh-CN', 'en']) {
  setLocale(locale);
  const host = dom.document.createElement('section');
  appendExecutionStatus(host, job, () => assert.fail('generic diagnostic'));
  const notice = errorText({code: job.error, diagnostic: detail});
  for (const fact of facts) {
    assert(host.textContent.includes(fact));
    assert(notice.detail.includes(fact));
  }
  assert.match(notice.title, locale === 'en' ? /processing limit/ : /可处理上限/);
}
dom.restore();
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=Path(__file__).resolve().parents[2],
        input=json.dumps(
            {key: public[key] for key in ("state", "error", "error_detail")}, ensure_ascii=False
        ),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_real_capacity_failure_reaches_progress_http_with_exact_moc_and_missing_members(
    config, ready
):
    cid, vid, root, moc, note = ready
    moved = root / "Resource/original.md"
    note.rename(moved)
    history = cid, vid, root, moc, moved
    missing, target = large_missing_history(config, history, repetitions=18)
    with pytest.raises(ObservationError, match="moc_retained_member_limit") as failure:
        captured(config, history, target)
    actual = failure.value.detail
    assert actual["actual_bytes"] > actual["limit_bytes"] == 24 * 1024
    assert set(actual["missing_members"]) == {entity["path"] for entity in missing}
    expected = {
        "reason": "retained_evidence_limit",
        "phase": "moc_retained_members",
        "path": target["path"],
        "moc_id": target["id"],
        "actual_bytes": actual["actual_bytes"],
        "limit_bytes": actual["limit_bytes"],
        "missing_members": actual["missing_members"],
        "changed_fields": [],
        "retryable_read": False,
    }
    # The actual capture exception also traverses both bounded error boundaries.
    failure.value.detail.update(block="PRIVATE PROOF TEXT", registration={"private": True})
    packet = response(failure.value)
    assert packet.status_code == 503
    assert json.loads(packet.body) == {
        "error": "moc_retained_member_limit",
        "error_detail": expected,
    }
    assert read_errors.response(failure.value) == (503, json.loads(packet.body))
    assert read_errors.response(IntegrityError("moc_retained_member_limit")) == (
        503,
        {"error": "moc_retained_member_limit"},
    )

    (root / "Inbox/new.md").write_text("# New thermal note\n#热管理\nOriginal source remains.\n")
    sync_store.enqueue(config, vid, [("", 1)], "20", now=time.time())
    while tasks := sync_store.due(config, now=time.time() + 100):
        for task in tasks:
            scanner.apply(config, task)
    scanner.heartbeat(config, vid)
    with database.connect(config.state_root) as db:
        job = preview(config, db, cid, vid, "quick")
        assert len(job["items"]) == 1 and job["items"][0]["add"] == [target["id"]]
        maintenance.action(config, db, job, "confirm", job["plan_sha256"])
        before_files = {p: p.read_bytes() for p in root.rglob("*.md")}
        members = moc_store.load(config, vid, target["id"])
        result = maintenance.run(config, db, job)
    assert result["state"] == "partial" and result["completed"] == 0
    assert result["error"] == "moc_retained_member_limit"
    assert moc_store.load(config, vid, target["id"]) == members
    assert all(path.read_bytes() == data for path, data in before_files.items())
    before_job = deepcopy(store.get(config, "jobs", job["id"]))
    auth.set_password(config, "synthetic-retained-diagnostic-password")
    with TestClient(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 49000)
    ) as http:
        login = http.post(
            "/admin/login",
            json={"password": "synthetic-retained-diagnostic-password"},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert login.status_code == 200, login.text
        packet = http.get(
            "/admin/organization/progress", params={"client_id": cid, "vault_id": vid}
        )
        assert packet.status_code == 200, packet.text
        public = next(value for value in packet.json()["jobs"] if value["id"] == job["id"])
        assert public["state"] == "partial" and public["completed"] == 0
        assert public["error"] == "moc_retained_member_limit"
        assert {key: public["error_detail"][key] for key in expected} == expected
        assert_frontend_evidence(public)
    assert store.get(config, "jobs", job["id"]) == before_job
    assert moc_store.load(config, vid, target["id"]) == members
    assert all(path.read_bytes() == data for path, data in before_files.items())
