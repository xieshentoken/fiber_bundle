"""Batch stages remain distinct from Store and publication, across shared platforms."""

import asyncio
from types import SimpleNamespace

from test_execution import policy as policy
from test_feishu_ingress import archive, freeze, payload
from test_feishu_ingress import feishu as feishu
from test_weixin_inbox import binding as binding

from fiber_bundle import admin_batches, feishu_receiver, weixin_batches, weixin_store


def test_one_zip_event_counts_verified_files_members_and_stored_stage(config, feishu):
    feishu_receiver.capture(
        config, feishu, payload("file", {"file_key": "file", "file_name": "chat.zip"})
    )
    import io

    class Adapter:
        _client = SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(message_resource=SimpleNamespace(get=None)))
        )

        def _build_message_resource_request(self, **kwargs):
            return kwargs

        async def _run_blocking(self, *_):
            return SimpleNamespace(success=lambda: True, file=io.BytesIO(archive()))

    asyncio.run(feishu_receiver.download_one(config, feishu, Adapter()))
    batch = freeze(config, feishu)
    view = admin_batches.pending(config)[0]
    assert view["event_count"] == 1 and view["received_file_count"] == 1
    assert view["archive_member_count"] == 2
    assert "青松" in view["preview"] and "2.4 MPa" in view["preview"]
    assert view["platform"] == "feishu" and view["client_id"] == feishu.client_id
    weixin_batches.choose(config, feishu, batch, "save_one")
    selected = admin_batches.pending(config, include_selected=True)[0]
    assert selected["store_state"] == "not_started"
    assert selected["processing_blocker"] == "receiver_unavailable"
    assert admin_batches.pending(config) == []  # Legacy pending API remains compatible.
    weixin_store.transfer(config, feishu, batch)
    saved = admin_batches.pending(config, include_selected=True)[0]
    assert saved["store_state"] == "stored"
    assert saved["publication_state"] == "not_published"
    assert len(saved["receipts"]) == 1 and saved["chat_message_count"] is None


def test_batch_projection_follows_actual_candidate_and_publication(config, feishu, policy):
    from test_source_processing import complete, prepare

    from fiber_bundle import (
        auth,
        database,
        document_evidence,
        document_publication,
        document_review,
    )

    feishu_receiver.capture(config, feishu, payload(body={"text": "青松\n测试压力 2.4 MPa"}))
    batch = freeze(config, feishu)
    weixin_batches.choose(config, feishu, batch, "save_one")
    receipt = weixin_store.transfer(config, feishu, batch)["receipts"][0]["job_id"]
    db = database.connect(config.state_root)
    auth.set_grant(db, feishu.client_id, feishu.vault_id, query=True, original=True, add=True)
    _, rid = prepare(config, db, policy, feishu.client_id, feishu.vault_id, receipt)
    pending = admin_batches.pending(config, include_selected=True)[0]
    assert pending["processing_state"] == "queued"
    assert pending["publication_state"] == "not_published"
    complete(config, db, policy, rid)
    candidate = document_evidence.load(config, db, feishu.client_id, rid)[1]
    extracted = admin_batches.pending(config, include_selected=True)[0]
    assert extracted["processing_state"] == "succeeded"
    assert extracted["tasks"][0]["candidate_state"] == "extracted"
    assert extracted["publication_state"] == "not_published"
    document_review.record(
        db,
        rid,
        candidate,
        dict(
            source_sha256=candidate["source_sha256"],
            result_sha256=candidate["result_sha256"],
            source_reviewed=True,
            visual_reviewed=True,
            judgments=[
                dict(unit_id=u["unit_id"], verdict="accept", reason="synthetic source checked")
                for u in document_review.units(candidate)
            ],
        ),
    )
    assert document_publication.publish_locked(config, db, rid)["state"] == "published"
    db.close()
    published = admin_batches.pending(config, include_selected=True)[0]
    assert published["publication_state"] == "published"
    assert published["store_state"] == "stored"
