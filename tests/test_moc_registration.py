"""Adoption is previewed, byte-preserving, permission scoped, and recoverable."""

from unittest.mock import patch

import pytest
from test_article_tasks import article_scope as article_scope
from test_execution import policy as policy
from test_publication_layout import activate

from fiber_bundle import (
    auth,
    database,
    moc_registration,
    moc_store,
    note_identities,
    note_placement,
)
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.objects import digest
from fiber_bundle.vault_save_contract import settings_wire, unified_settings


@pytest.fixture
def sample(config, article_scope):
    with database.connect(config.state_root) as db:
        auth.set_grant(db, *article_scope[:2], query=True, original=True, add=True)
    settings = settings_wire(unified_settings())
    settings.pop("layout_mode")
    activate(config, article_scope[1], **settings)
    root = config.project_root / "test vault/temp_vault1"
    path = root / "Map/人工目录.md"
    raw = "---\r\ntype: moc\r\n---\r\n# 人工目录\r\n\r\n## 人工分类\r\n[[其他笔记]]\r\n".encode()
    path.write_bytes(raw)
    return article_scope, path, raw


@pytest.mark.parametrize("tracked", [False, True])
def test_preview_then_adopt_preserves_manual_bytes_and_identity(config, sample, tracked):
    scope, path, raw = sample
    if tracked:
        with database.connect(config.state_root) as db:
            c = auth.create_client(db, "synthetic adoption", False)
            auth.set_grant(db, c["id"], scope[1], query=True, original=True, add=True)
        note_identities.run(
            config, "Bearer " + c["token"], scope[1], "track", "Map/人工目录.md", digest(raw)
        )
    with database.connect(config.state_root) as db:
        listing = moc_registration.listing(config, db, *scope[:2])
        assert listing["candidates"][0]["moc_id"] is None
        p = moc_registration.preview(config, db, *scope[:2], "Map/人工目录.md", digest(raw))
        assert path.read_bytes() == raw and moc_store.load(config, scope[1], p["moc_id"]) is None
        r = moc_registration.apply(config, db, *scope[:2], p["operation_id"], "apply")
        assert r["state"] == "completed" and path.read_bytes().startswith(raw)
        assert (
            moc_registration.listing(config, db, *scope[:2])["candidates"][0]["moc_id"]
            == p["moc_id"]
        )
        assert note_placement.listing(config, db, *scope[:2])["mocs"][0]["id"] == p["moc_id"]
        again = moc_registration.apply(config, db, *scope[:2], p["operation_id"], "apply")
        assert again == {"state": "completed", "moc_id": p["moc_id"]}
        with pytest.raises(InvalidInput, match="already_registered"):
            moc_registration.preview(
                config, db, *scope[:2], "Map/人工目录.md", digest(path.read_bytes())
            )


@pytest.mark.parametrize("fault", ["changed", "revoked", "expired", "foreign"])
def test_changed_or_unauthorized_adoption_never_writes(config, sample, fault):
    scope, path, raw = sample
    with database.connect(config.state_root) as db:
        p = moc_registration.preview(config, db, *scope[:2], "Map/人工目录.md", digest(raw))
        if fault == "changed":
            path.write_bytes(raw + b"manual edit\n")
        if fault == "revoked":
            auth.set_grant(db, *scope[:2], query=True, original=True, add=False)
        if fault == "foreign":
            with pytest.raises(InvalidInput):
                moc_registration.preview(config, db, *scope[:2], "Other/x.md", digest(raw))
            return
        before = path.read_bytes()
        with (
            patch("fiber_bundle.moc_registration.time.time", return_value=10**12)
            if fault == "expired"
            else __import__("contextlib").nullcontext()
        ):
            with pytest.raises((IntegrityError, auth.Denied)):
                moc_registration.apply(config, db, *scope[:2], p["operation_id"], "apply")
        assert path.read_bytes() == before and moc_store.load(config, scope[1], p["moc_id"]) is None


def test_explicit_recovery_after_file_exchange(config, sample):
    scope, path, raw = sample
    from fiber_bundle import moc_file_journal

    original = moc_file_journal.finish
    with database.connect(config.state_root) as db:
        p = moc_registration.preview(config, db, *scope[:2], "Map/人工目录.md", digest(raw))
        with (
            patch.object(moc_file_journal, "finish", side_effect=OSError("simulated crash")),
            pytest.raises(OSError),
        ):
            moc_registration.apply(config, db, *scope[:2], p["operation_id"], "apply")
        assert path.read_bytes().startswith(raw)
        assert moc_registration.listing(config, db, *scope[:2])["operations"]
        with patch.object(moc_file_journal, "finish", original):
            result = moc_registration.apply(config, db, *scope[:2], p["operation_id"], "reconcile")
        assert result["state"] == "completed"
        assert path.read_bytes().count(b"<!-- fiber-moc:") == 2


def test_http_requires_admin_csrf_and_explicit_confirmation(config, sample):
    from fastapi.testclient import TestClient

    from fiber_bundle.api import create_app

    scope, path, raw = sample
    client = TestClient(
        create_app(config), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 20000)
    )
    body = {
        "client_id": scope[0],
        "vault_id": scope[1],
        "path": "Map/人工目录.md",
        "expected_sha256": digest(raw),
    }
    url = "/admin/vault-placements/mocs"
    client.headers["Origin"] = "http://127.0.0.1:8765"
    assert client.post(url + "/preview", json=body).status_code == 401
    auth.set_password(config, "synthetic-moc-adoption-password")
    login = client.post("/admin/login", json={"password": "synthetic-moc-adoption-password"})
    assert client.post(url + "/preview", json=body).status_code == 403
    client.headers["X-CSRF-Token"] = login.json()["csrf"]
    prepared = client.post(url + "/preview", json=body)
    assert prepared.status_code == 200, prepared.text
    target = url + "/" + prepared.json()["operation_id"] + "/apply"
    assert (
        client.post(
            target,
            json={k: v for k, v in body.items() if k in {"client_id", "vault_id"}}
            | {"confirm": False},
        ).status_code
        == 409
    )
    assert path.read_bytes() == raw
    result = client.post(
        target, json={"client_id": scope[0], "vault_id": scope[1], "confirm": True}
    )
    assert result.status_code == 200 and result.json()["state"] == "completed"
