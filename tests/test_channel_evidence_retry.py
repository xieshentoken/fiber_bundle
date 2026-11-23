"""Local cloud failures retain paid responses; failures never bypass source guards."""

import json

import pytest
from test_channel_natural_query import model_channel as model_channel
from test_channel_natural_query import result
from test_channel_query_recovery import capture, responding
from test_message_channels import channel as channel
from test_model_configuration import vault_keychain as vault_keychain

from fiber_bundle import auth, database
from fiber_bundle.channels import dispatch, natural_query, store
from fiber_bundle.config import IntegrityError


@pytest.mark.parametrize("platform", ["feishu", "weixin"])
@pytest.mark.parametrize(
    "reason",
    [
        "cloud_metadata_unavailable",
        "cloud_status_check_timeout",
        "cloud_item_pending_stale_or_conflicted",
    ],
)
def test_transient_local_failure_resumes_with_single_paid_plan_and_verified_answer(
    config, model_channel, monkeypatch, platform, reason
):
    value, _ = model_channel
    with store.opened(config) as db:
        current = store.get(db, value["id"])
        current["value"]["platform"] = platform
        db.execute(
            "UPDATE channels SET value=? WHERE id=?", (json.dumps(current["value"]), value["id"])
        )
    calls = responding(monkeypatch)
    original = natural_query.evidence
    reads = []

    def evidence(*args):
        reads.append(True)
        if len(reads) == 1:
            raise IntegrityError(reason)
        return original(*args)

    monkeypatch.setattr(natural_query, "evidence", evidence)
    capture(config, value)
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "processing" and "result" not in data
    assert data["query_processing"]["phase"] == "evidence"
    assert data["local_evidence_retry"]["attempts"] == 1
    assert len(calls) == 1
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "ready" and data["result"]["citation_integrity"] == "verified"
    assert len(reads) == 2 and len(calls) == len(set(calls)) == 2
    with database.connect(config.state_root) as db:
        assert db.execute("SELECT count(*) FROM model_budget_reservations").fetchone()[0] == 2
    terminal = result(config, value)
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value) == terminal and len(calls) == 2


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("cloud_metadata_unavailable", "channel_cloud_metadata_unavailable"),
        ("cloud_status_check_timeout", "channel_cloud_check_timeout"),
        ("cloud_item_pending_stale_or_conflicted", "channel_cloud_not_ready"),
    ],
)
def test_persistent_cloud_failure_stops_after_one_durable_local_retry(
    config, model_channel, monkeypatch, reason, code
):
    value, _ = model_channel
    calls = responding(monkeypatch)
    reads = []

    def unavailable(*args):
        reads.append(True)
        raise IntegrityError(reason)

    monkeypatch.setattr(natural_query, "evidence", unavailable)
    capture(config, value)
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[0] == "processing"
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "ready" and data["error_code"] == code
    assert data["failure"]["integrity_reason"] == reason
    assert "已完成一次本地重查" in data["result"]["answer"]
    assert data["result"]["sources"] == [] and len(calls) == 1
    dispatch.advance(config, value["id"], value["revision"])
    assert len(reads) == 2 and len(calls) == 1


@pytest.mark.parametrize(
    ("reason", "code", "diagnostic"),
    [
        (
            "search evidence changed before delivery",
            "channel_source_changed",
            "search evidence changed before delivery",
        ),
        (
            "search index passage differs from current evidence",
            "channel_source_changed",
            "search index passage differs from current evidence",
        ),
        ("cloud_item_not_downloaded", "channel_cloud_not_downloaded", "cloud_item_not_downloaded"),
        (
            "private-path credential-secret",
            "channel_model_query_failed",
            "unclassified_integrity_error",
        ),
    ],
)
def test_changed_unavailable_or_unknown_source_never_retries_or_leaks_arbitrary_text(
    config, model_channel, monkeypatch, reason, code, diagnostic
):
    value, _ = model_channel
    calls = responding(monkeypatch)

    def unavailable(*args):
        raise IntegrityError(reason)

    monkeypatch.setattr(natural_query, "evidence", unavailable)
    capture(config, value)
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "ready" and data["error_code"] == code
    assert data["failure"]["integrity_reason"] == diagnostic
    assert "local_evidence_retry" not in data
    assert "credential-secret" not in json.dumps(data)
    assert data["result"]["sources"] == [] and len(calls) == 1


@pytest.mark.parametrize("change", ["cache_removed", "revoked", "retry_corrupt"])
def test_local_recovery_rechecks_cache_permission_and_durable_retry_bound(
    config, model_channel, monkeypatch, change
):
    from fiber_bundle.channels.validation import ChannelValidationError

    value, _ = model_channel
    calls = responding(monkeypatch)
    original = natural_query.evidence

    def unavailable(*args):
        raise IntegrityError("cloud_metadata_unavailable")

    monkeypatch.setattr(natural_query, "evidence", unavailable)
    capture(config, value)
    dispatch.advance(config, value["id"], value["revision"])
    assert result(config, value)[0] == "processing"
    monkeypatch.setattr(natural_query, "evidence", original)
    if change == "cache_removed":
        (config.state_root / "channel-query-results" / (calls[0] + ".json")).unlink()
    elif change == "revoked":
        with database.connect(config.state_root) as db:
            auth.revoke_client(db, value["settings"]["client_id"])
    else:
        with store.opened(config) as db:
            data = result(config, value)[1]
            data["local_evidence_retry"]["attempts"] = -1
            db.execute(
                "UPDATE events SET value=? WHERE channel_id=?", (json.dumps(data), value["id"])
            )
    if change == "revoked":
        with pytest.raises((auth.Denied, ChannelValidationError)):
            dispatch.advance(config, value["id"], value["revision"])
        assert result(config, value)[0] == "processing"
    else:
        dispatch.advance(config, value["id"], value["revision"])
        _, data = result(config, value)
        assert data["error_code"] == "channel_model_result_unknown"
        assert data["result"]["sources"] == []
    assert len(calls) == 1


def test_cloud_failure_after_sources_are_frozen_does_not_restart_discovery(
    config, model_channel, monkeypatch
):
    value, _ = model_channel
    calls = responding(monkeypatch)

    def unavailable(*args):
        raise IntegrityError("cloud_metadata_unavailable")

    monkeypatch.setattr(natural_query, "recheck_evidence", unavailable)
    capture(config, value)
    dispatch.advance(config, value["id"], value["revision"])
    state, data = result(config, value)
    assert state == "ready" and data["error_code"] == "channel_cloud_metadata_unavailable"
    assert data["failure"]["phase"] == "answer_call"
    assert data["result"]["sources"] == []
    assert "local_evidence_retry" not in data and len(calls) == 1
