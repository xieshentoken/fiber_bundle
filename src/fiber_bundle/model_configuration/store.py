"""Transactional model records and stable shared budgets; no provider calls."""

import json
import secrets as random
import sqlite3
import time
from typing import Any
from uuid import uuid4

from .. import database, model_budgets
from ..config import Config
from ..inputs import canonical
from . import legacy, providers, secrets
from .contracts import Change, Save
from .errors import ModelError


def get(db: sqlite3.Connection, identity: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM model_configurations WHERE id=?", (identity,)).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise ModelError("model_unconfigured")
    if json.loads(row["settings_json"]).get("deleted"):
        raise ModelError("model_unconfigured")
    return row


def default(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute(
        "SELECT revision,value FROM model_configuration_settings WHERE key='default'"
    ).fetchone()
    return (
        {"revision": row[0], "configuration_id": json.loads(row[1])}
        if row
        else {"revision": 0, "configuration_id": None}
    )


def references(db: sqlite3.Connection, identity: str) -> list[dict[str, str]]:
    result = []
    if default(db)["configuration_id"] == identity:
        result.append({"kind": "default", "name": "应用默认模型"})
    for row in db.execute(
        "SELECT id,configuration_json FROM agent_connections WHERE state!='revoked'"
    ):
        data = json.loads(row[1])
        if data.get("model_configuration_id") == identity:
            result.append({"kind": "agent", "id": row[0], "name": data["name"]})
    # Video settings are file-backed. Read only this instance's active selections.
    from pathlib import Path

    location = next((r[2] for r in db.execute("PRAGMA database_list") if r[1] == "main"), "")
    if location:
        path = Path(location).parent / "configs/video-skill.json"
        if path.is_file():
            value = json.loads(path.read_bytes())
            if value.get("vision_model_configuration_id") == identity:
                result.append({"kind": "video", "name": "视频图片提取模型"})
            if (
                value.get("asr_backend") == "transcription-api"
                and value.get("asr_api_model_configuration_id") == identity
            ):
                result.append({"kind": "asr", "name": "视频 ASR 转写模型"})
    return result


def inspect(db: sqlite3.Connection) -> dict[str, Any]:
    keys = []
    for row in db.execute("SELECT * FROM model_credentials ORDER BY created_at"):
        keys.append(
            {
                "id": row["id"],
                "revision": row["revision"],
                "generation": row["generation"],
                "active": bool(row["active"]),
                **json.loads(row["settings_json"]),
            }
        )
    models = []
    for row in db.execute("SELECT * FROM model_configurations ORDER BY created_at"):
        settings = json.loads(row["settings_json"])
        verification = json.loads(row["verification_json"]) if row["verification_json"] else None
        credential = next((key for key in keys if key["id"] == row["credential_id"]), None)
        if verification and (
            not credential
            or verification.get("credential_generation") != credential["generation"]
            or verification.get("configuration_revision") != row["revision"]
        ):
            verification = None
        models.append(
            {
                "id": row["id"],
                "revision": row["revision"],
                "credential_id": row["credential_id"],
                "active": bool(row["active"]),
                "verification": verification,
                "references": references(db, row["id"]),
                **settings,
            }
        )
    return {
        "schema": "fiber-model-catalog/v1",
        "credentials": keys,
        "models": models,
        "default": default(db),
        "default_budget_tokens": 100000,
    }


def save(config: Config, db: sqlite3.Connection, value: Save) -> dict[str, Any]:
    connection = providers.connection(value)
    providers.check_effort(
        connection["provider"], value.model, connection["protocol"], value.effort
    )
    prior = (
        db.execute(
            "SELECT * FROM model_configurations WHERE id=?", (value.configuration_id,)
        ).fetchone()
        if value.configuration_id
        else None
    )
    if prior and json.loads(prior["settings_json"]).get("deleted"):
        raise ModelError("model_unconfigured")
    if prior and value.expected_revision == 0 and prior["revision"] == 1:
        previous = json.loads(prior["settings_json"])
        credential = db.execute(
            "SELECT * FROM model_credentials WHERE id=?", (prior["credential_id"],)
        ).fetchone()
        matching = bool(
            credential
            and json.loads(credential["settings_json"]) == {**connection, "name": value.name}
            and previous["model"] == value.model
            and previous["effort"] == value.effort
        )
        if matching and value.api_key:
            _, key = secrets.master(db)
            matching = credential["fingerprint"] == secrets.fingerprint(
                key, connection, value.api_key
            )
        elif matching:
            matching = (
                value.credential_id == prior["credential_id"]
                and credential["revision"] == value.credential_revision
            )
        if matching:
            return next(m for m in inspect(db)["models"] if m["id"] == prior["id"])
    if (prior["revision"] if prior else 0) != value.expected_revision:
        raise ModelError("model_revision_conflict")
    path = None
    try:
        with database.transaction(db):
            credential = (
                db.execute(
                    "SELECT * FROM model_credentials WHERE id=?", (value.credential_id,)
                ).fetchone()
                if value.credential_id
                else None
            )
            if value.credential_id and (
                not credential or credential["revision"] != value.credential_revision
            ):
                raise ModelError("model_revision_conflict")
            if credential and not value.api_key:
                if not credential["active"]:
                    raise ModelError("model_credential_revoked")
                if {
                    k: json.loads(credential["settings_json"])[k] for k in connection
                } != connection:
                    raise ModelError("model_revision_conflict")
            else:
                if value.provider != "local" and not value.api_key:
                    raise ModelError("model_key_required")
                account, key = secrets.master(db, create=True)
                fingerprint = secrets.fingerprint(key, connection, value.api_key or "")
                duplicate = db.execute(
                    "SELECT * FROM model_credentials WHERE fingerprint=?", (fingerprint,)
                ).fetchone()
                if duplicate and (not credential or duplicate["id"] == credential["id"]):
                    credential = duplicate
                elif duplicate:
                    raise ModelError("model_revision_conflict")
                else:
                    inherited = (
                        legacy.budget_identity(config, db, connection, value.api_key or "")
                        if not credential and value.api_key
                        else None
                    )
                    identity = (
                        credential["id"]
                        if credential
                        else inherited or "key_" + random.token_hex(32)
                    )
                    generation = credential["generation"] + 1 if credential else 1
                    path = secrets.seal(
                        config, identity, generation, account, key, value.api_key or ""
                    )
                    db.execute(
                        "INSERT INTO model_credentials VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(id) DO "
                        "UPDATE SET "
                        "revision=revision+1,generation=excluded.generation,settings_json=excluded.settings_json,fingerprint=excluded.fingerprint,secret_file=excluded.secret_file,active=1",
                        (
                            identity,
                            1,
                            generation,
                            canonical({**connection, "name": value.name}),
                            fingerprint,
                            str(path),
                            time.time(),
                        ),
                    )
                    budget = model_budgets.get(db, identity)
                    settings = (
                        model_budgets.Policy.model_validate(
                            {**budget["settings"], "label": value.name}
                        )
                        if budget
                        else model_budgets.Policy(label=value.name, limit=value.budget_tokens)
                    )
                    model_budgets.save(
                        db,
                        model_budgets.Save(
                            secret_file=str(path),
                            credential_id=identity,
                            expected_revision=budget["revision"] if budget else 0,
                            settings=settings,
                        ),
                    )
                    if inherited:
                        model_budgets.adopt_history(db, identity, identity, settings)
                    credential = db.execute(
                        "SELECT * FROM model_credentials WHERE id=?", (identity,)
                    ).fetchone()
            assert credential is not None
            # Label updates do not touch the budget accounting basis or reservations.
            label_settings = {**json.loads(credential["settings_json"]), "name": value.name}
            if label_settings != json.loads(credential["settings_json"]):
                db.execute(
                    "UPDATE model_credentials SET settings_json=?,revision=revision+1 WHERE id=?",
                    (canonical(label_settings), credential["id"]),
                )
                budget = model_budgets.get(db, credential["id"])
                assert budget is not None
                model_budgets.save(
                    db,
                    model_budgets.Save(
                        secret_file=credential["secret_file"],
                        credential_id=credential["id"],
                        expected_revision=budget["revision"],
                        settings=model_budgets.Policy.model_validate(
                            {**budget["settings"], "label": value.name}
                        ),
                    ),
                )
            identity = value.configuration_id or "model_" + uuid4().hex
            profile = {
                "model": value.model,
                "effort": value.effort,
                "capabilities": providers.capabilities(
                    connection["provider"], value.model, connection["protocol"]
                ),
            }
            db.execute(
                "INSERT INTO model_configurations VALUES(?,?,?,?,1,NULL,?) ON CONFLICT(id) "
                "DO UPDATE SET "
                "revision=revision+1,credential_id=excluded.credential_id,settings_json=excluded.settings_json,verification_json=NULL",
                (
                    identity,
                    value.expected_revision + 1,
                    credential["id"],
                    canonical(profile),
                    time.time(),
                ),
            )
        return next(m for m in inspect(db)["models"] if m["id"] == identity)
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise


def change(db: sqlite3.Connection, identity: str, value: Change) -> dict[str, Any]:
    row = get(db, identity)
    if row["revision"] != value.expected_revision:
        raise ModelError("model_revision_conflict")
    with database.transaction(db):
        if value.action == "default":
            if (
                value.expected_default_revision is not None
                and default(db)["revision"] != value.expected_default_revision
            ):
                raise ModelError("model_revision_conflict")
            if not row["active"]:
                raise ModelError("model_disabled")
            if not json.loads(row["settings_json"])["capabilities"]["compatible"]:
                raise ModelError("model_protocol_unsupported")
            db.execute(
                "INSERT INTO model_configuration_settings VALUES('default',1,?) ON "
                "CONFLICT(key) DO UPDATE SET revision=revision+1,value=excluded.value",
                (canonical(identity),),
            )
        elif value.action in ("disable", "delete"):
            if references(db, identity):
                raise ModelError("model_referenced")
            db.execute(
                "UPDATE model_configurations SET "
                "active=0,revision=revision+1,verification_json=NULL WHERE id=?",
                (identity,),
            )
            if value.action == "delete":
                settings = {**json.loads(row["settings_json"]), "deleted": True}
                db.execute(
                    "UPDATE model_configurations SET settings_json=? WHERE id=?",
                    (canonical(settings), identity),
                )
        else:
            db.execute(
                "UPDATE model_configurations SET active=1,revision=revision+1 WHERE id=?",
                (identity,),
            )
    return inspect(db)
