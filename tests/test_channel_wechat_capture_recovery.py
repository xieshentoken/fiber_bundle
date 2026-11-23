"""Synthetic WeChat transport failures through Feishu intake, Store, and indexing."""

import json
import re

import pytest
from test_article_fetch import Reply, install
from test_articles import PNG, TEXT
from test_automatic_chat import drain
from test_channel_intake import count, enable, receive, records
from test_channel_intake import entry as entry
from test_channel_intake_content import grant_original
from test_publication_layout import activate
from test_weixin_inbox import binding as binding

from fiber_bundle import (
    article_fetch,
    automatic_ingestion,
    automatic_policy,
    database,
    objects,
    search,
    vault_save_contract,
    weixin_inbox,
)
from fiber_bundle.channels import intake_extraction as extraction
from fiber_bundle.channels import intake_processing as processing
from fiber_bundle.channels import intake_repository
from fiber_bundle.channels import store as channels
from fiber_bundle.store import Store

SOURCE = "https://mp.weixin.qq.com/s/recovery-synthetic"
TITLE = "Synthetic capture recovery"
IMAGE_TARGETS = [f"/{i:02d}.png" for i in range(1, 11)]


class WorkerStopped(BaseException):
    """Simulate process loss before ordinary error handling can update the link."""


def article():
    raw = (
        f'<h1 id="activity-name">{TITLE}</h1><div id="js_content">{TEXT}'
        + "".join(f'<p>{i}</p><img src="https://mmbiz.qpic.cn{i}">' for i in IMAGE_TARGETS)
        + "<p>末段完整保留</p></div>"
    ).encode()
    return Reply(200, raw, [("Content-Type", "text/html")])


def image():
    return Reply(200, PNG, [("Content-Type", "image/png")])


@pytest.fixture
def admitted(config, entry, monkeypatch):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)
    monkeypatch.setattr(article_fetch.time, "sleep", lambda delay: None)
    grant_original(config, entry)
    settings = vault_save_contract.settings_wire(vault_save_contract.unified_settings())
    settings.pop("layout_mode")
    activate(config, entry[1].vault_id, **settings)
    with database.connect(config.state_root) as db:
        automatic_policy.save(
            config,
            db,
            entry[1].vault_id,
            0,
            automatic_policy.Policy(
                policy_version=2,
                enabled=True,
                direct_to_inbox=True,
                client_id=entry[1].client_id,
                allowed_kinds=["intake"],
            ),
        )
    enable(config, entry, policy_version=2, fetch_articles=True, store_limit=0, download_limit=0)
    batch = receive(config, entry, text=SOURCE)
    assert processing.advance(config, entry[0]["id"]) == 1
    return batch, original(config, entry)


def original(config, entry):
    with weixin_inbox.connection(config, entry[1]) as db:
        events = [dict(r) for r in db.execute("SELECT * FROM events")]
    with database.connect(config.state_root) as db:
        saved = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM receipts WHERE ingress_scope NOT LIKE 'channel-intake-extraction:%'"
            )
        ]
        assert len(saved) == 1
        receipt = Store(config).verify_receipt_locked(db, saved[0]["job_id"])
        raw = [
            objects.read_object(
                config.state_root,
                objects.object_relative(entry[1].vault_id, asset["sha256"]),
                asset["sha256"],
                asset["byte_count"],
            )
            for asset in receipt["assets"]
        ]
    return events, saved, raw


def link(config, entry):
    return json.loads(records(config, entry)[0]["value"])["links"][0]


def downloads(config, entry):
    with channels.readonly(config) as db:
        return intake_repository.consumed(db, entry[0]["id"])["download"]


def assert_published(config, entry, admitted):
    _, original_before = admitted
    drain(config)
    saved = link(config, entry)
    assert saved["metadata"]["image_count"] == 10
    assert saved["publication"]["state"] == "published"
    vault = config.vault_path("test vault/temp_vault1")
    path = vault / saved["publication"]["entry_note_path"]
    note = path.read_text()
    assert "末段完整保留" in note
    images = re.findall(r"!\[\[(Attachments/images/[^\]]+)\]\]", note)
    assert len(set(images)) == 10 and all((vault / name).read_bytes() == PNG for name in images)
    with database.connect(config.state_root) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM automatic_jobs WHERE phase='index' AND state='complete'"
            ).fetchone()[0]
            == 1
        )
        assert search.vault_search(config, db, entry[1].client_id, TITLE, [entry[1].vault_id])[
            "results"
        ]
    assert count(config, "publication_locations") == 1
    assert count(config, "receipts") == 2
    assert count(config, "model_calls") == count(config, "execution_runs") == 0
    assert original(config, entry) == original_before
    assert not extraction.advance(config, entry[0]["id"])
    drain(config)
    assert count(config, "receipts") == 2
    assert count(config, "publication_locations") == 1


def test_timeout_retries_only_failed_image_then_publishes_once(
    config, entry, admitted, monkeypatch
):
    calls, closed = install(
        monkeypatch,
        [article(), *[image() for _ in range(9)], TimeoutError("private"), image()],
    )
    assert extraction.advance(config, entry[0]["id"])
    assert [c[2] for c in calls] == ["/s/recovery-synthetic", *IMAGE_TARGETS, IMAGE_TARGETS[-1]]
    assert len(closed) == 12
    assert downloads(config, entry) == 11
    assert_published(config, entry, admitted)


@pytest.mark.parametrize("manual", [False, True], ids=["next-worker-tick", "explicit-retry"])
def test_interrupted_capture_resumes_missing_image_and_keeps_attempt(
    config, entry, admitted, monkeypatch, manual
):
    calls, _ = install(monkeypatch, [article(), *[image() for _ in range(9)]])
    connection = article_fetch.PublicConnection

    def interrupt(host, *args, **kwargs):
        if len(calls) == 10:
            raise WorkerStopped
        return connection(host, *args, **kwargs)

    monkeypatch.setattr(article_fetch, "PublicConnection", interrupt)
    with pytest.raises(WorkerStopped):
        extraction.advance(config, entry[0]["id"])
    assert [c[2] for c in calls] == ["/s/recovery-synthetic", *IMAGE_TARGETS[:9]]
    interrupted = link(config, entry)
    assert interrupted["state"] == "extracting"
    assert count(config, "receipts") == 1 and count(config, "publication_locations") == 0
    assert downloads(config, entry) == 11
    if manual:
        batch, _ = admitted
        extraction.retry(config, entry[0]["id"], batch, 0, records(config, entry)[0]["revision"])

    calls, _ = install(monkeypatch, [image()])
    assert extraction.advance(config, entry[0]["id"])
    assert [c[2] for c in calls] == IMAGE_TARGETS[-1:]
    assert link(config, entry)["attempt_id"] == interrupted["attempt_id"]
    assert downloads(config, entry) == 11
    assert_published(config, entry, admitted)


def test_explicit_recovery_after_exhaustion_only_fetches_missing_image(
    config, entry, admitted, monkeypatch, http
):
    calls, _ = install(
        monkeypatch,
        [article(), *[image() for _ in range(9)], *[TimeoutError("private") for _ in range(3)]],
    )
    assert extraction.advance(config, entry[0]["id"])
    assert [c[2] for c in calls] == [
        "/s/recovery-synthetic",
        *IMAGE_TARGETS,
        *IMAGE_TARGETS[-1:] * 2,
    ]
    failed = link(config, entry)
    assert failed["state"] == "needs_review"
    assert failed["reason"] == "intake_article_image_timeout"
    assert "private" not in json.dumps(failed["diagnostic"])
    assert count(config, "receipts") == 1 and count(config, "publication_locations") == 0
    drain(config)
    assert count(config, "publication_locations") == 0
    assert not extraction.advance(config, entry[0]["id"])

    batch, _ = admitted
    retry = http.post(
        f"/admin/channels/{entry[0]['id']}/intake/batches/{batch}/links/0/retry",
        json={"expected_revision": records(config, entry)[0]["revision"]},
    )
    assert retry.status_code == 202, retry.text
    calls, _ = install(monkeypatch, [image()])
    assert extraction.advance(config, entry[0]["id"])
    assert [c[2] for c in calls] == IMAGE_TARGETS[-1:]
    assert downloads(config, entry) == 12
    assert_published(config, entry, admitted)


def test_committed_store_receipt_is_reconciled_without_network_or_second_receipt(
    config, entry, admitted, monkeypatch
):
    ingest = Store.ingest_locked

    def crash_after_commit(self, db, **kwargs):
        receipt = ingest(self, db, **kwargs)
        assert receipt.state == "stored"
        raise WorkerStopped

    monkeypatch.setattr(Store, "ingest_locked", crash_after_commit)
    install(monkeypatch, [article(), *[image() for _ in range(10)]])
    with pytest.raises(WorkerStopped):
        extraction.advance(config, entry[0]["id"])
    before = link(config, entry)
    assert before["state"] == "extracting" and count(config, "receipts") == 2
    with database.connect(config.state_root) as db:
        receipt_id = db.execute(
            "SELECT job_id FROM receipts WHERE event_id=?", (before["attempt_id"],)
        ).fetchone()[0]

    monkeypatch.setattr(Store, "ingest_locked", ingest)
    calls, _ = install(monkeypatch, [])
    assert extraction.advance(config, entry[0]["id"])
    assert calls == []
    recovered = link(config, entry)
    assert recovered["receipt"]["job_id"] == receipt_id
    assert recovered["attempt_id"] == before["attempt_id"]
    assert_published(config, entry, admitted)
