"""Real subprocess isolation with synthetic local notes; no platform/model/network IO."""

import json
import shutil
import time
from pathlib import Path
from uuid import uuid4

from test_message_channels import bind
from test_message_channels import channel as channel

from fiber_bundle import auth, automatic_ingestion, database
from fiber_bundle.channels import dispatch, store
from fiber_bundle.channels.services import control, policy, runtime_image


def wait_until(condition, timeout=12):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return
        time.sleep(0.05)
    assert condition()


def test_query_stop_and_backend_recovery_leave_intake_process_and_history(
    config, channel, tmp_path
):
    entry, root = channel
    ch = bind(config, entry)
    identity = ch["id"]
    with database.connect(config.state_root) as main:
        main.execute("UPDATE vaults SET read_only=0 WHERE id=?", (ch["value"]["vault_id"],))
        auth.set_grant(
            main,
            ch["value"]["client_id"],
            ch["value"]["vault_id"],
            query=True,
            original=False,
            add=True,
        )
    with store.opened(config) as db:
        db.execute(
            "UPDATE channels SET value=? WHERE id=?",
            (json.dumps({**ch["value"], "add": True}), identity),
        )
    before = (root / "a.md").read_bytes()
    assert dispatch.capture(config, identity, 1, "old-held", ch["owner"], "查询本库：压力")
    try:
        control.start(config, identity, "query", 0)
        control.start(config, identity, "intake", 0)
        wait_until(lambda: control.status(config, identity, "intake").get("heartbeat_fresh"))
        intake_home = control.home(config, identity, "intake")
        pid = json.loads((intake_home / "status.json").read_text())["pid"]
        assert dispatch.capture(config, identity, 1, "new", ch["owner"], "查询本库：压力")

        def state(event):
            with store.readonly(config) as db:
                return db.execute(
                    "SELECT state FROM events WHERE channel_id=? AND event_id=?", (identity, event)
                ).fetchone()[0]

        with store.opened(config) as db:
            db.execute("UPDATE channels SET state='paused' WHERE id=?", (identity,))
        wait_until(lambda: state("new") == "ready")
        from fiber_bundle.channels.control import offline_ready

        assert not offline_ready(config)  # Pausing reception alone cannot permit offline restore.
        from fiber_bundle.channels import health

        with database.connect(config.state_root) as main, store.readonly(config) as side:
            view = health.present(config, main, store.get(side, identity))
        assert view["services"]["roles"]["intake"]["process_running"]

        assert state("old-held") == "captured"
        with database.connect(config.state_root) as db:
            job = str(uuid4())
            db.execute(
                "INSERT INTO automatic_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job,
                    ch["value"]["vault_id"],
                    ch["value"]["client_id"],
                    "intake",
                    "source",
                    1,
                    "review",
                    "claimed",
                    json.dumps({"intake": {"channel": identity, "batch": "held"}}),
                    None,
                    time.time(),
                    time.time(),
                ),
            )
        automatic_ingestion.recover(config)
        with database.connect(config.state_root) as db:
            assert (
                db.execute("SELECT state FROM automatic_jobs WHERE id=?", (job,)).fetchone()[0]
                == "claimed"
            )
        control.pause(config, identity, "query", 1)
        wait_until(lambda: not control.status(config, identity, "query").get("process_running"))
        assert control.status(config, identity, "intake")["process_running"]
        assert json.loads((intake_home / "status.json").read_text())["pid"] == pid
        import fiber_bundle

        package = tmp_path / "updated/fiber_bundle"
        shutil.copytree(
            Path(fiber_bundle.__file__).parent,
            package,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        with (package / "__init__.py").open("a") as code:
            code.write("\n# Synthetic query upgrade\n")
        updated = runtime_image.pin(config, package=package)
        prior_image = json.loads((intake_home / "status.json").read_text())["image"]
        control.start(config, identity, "query", 2, image=updated)
        wait_until(lambda: control.status(config, identity, "query").get("heartbeat_fresh"))
        assert control.status(config, identity, "query")["runtime_version"] == updated["id"]
        assert updated["id"] != prior_image
        assert json.loads((intake_home / "status.json").read_text())["pid"] == pid
        assert control.status(config, identity, "intake")["runtime_version"] == prior_image
        assert (root / "a.md").read_bytes() == before
        assert state("old-held") == "captured"
    finally:
        for role in policy.ROLES:
            p = policy.get(config, identity, role)
            if p and p["enabled"]:
                control.pause(config, identity, role, p["revision"])
        for role in policy.ROLES:
            wait_until(
                lambda role=role: not control.status(config, identity, role).get("process_running")
            )


def test_pinned_images_survive_source_update(config, tmp_path):
    package = tmp_path / "fiber_bundle"
    package.mkdir()
    code = package / "version.py"
    code.write_text("VERSION = 1\n")
    first = runtime_image.pin(config, package=package)
    code.write_text("VERSION = 2\n")
    second = runtime_image.pin(config, package=package)
    assert first["id"] != second["id"]
    assert (
        Path(first["root"]) / first["site"] / "fiber_bundle/version.py"
    ).read_text() == "VERSION = 1\n"


def test_saving_new_batch_continues_with_query_paused_and_old_batch_held(config, channel):
    from test_channel_intake import enable, receive

    from fiber_bundle import feishu_receiver
    from fiber_bundle.channels import intake_repository

    entry, root = channel
    ch = bind(config, entry)
    identity = ch["id"]
    with database.connect(config.state_root) as main:
        main.execute("UPDATE vaults SET read_only=0 WHERE id=?", (ch["value"]["vault_id"],))
        auth.set_grant(
            main,
            ch["value"]["client_id"],
            ch["value"]["vault_id"],
            query=True,
            original=False,
            add=True,
        )
    with store.opened(config) as db:
        db.execute(
            "UPDATE channels SET value=? WHERE id=?",
            (json.dumps({**ch["value"], "add": True}), identity),
        )
        ch = store.get(db, identity)
    with store.opened(config) as db:
        db.execute("UPDATE channels SET owner='ou_owner' WHERE id=?", (identity,))
        ch = store.get(db, identity)
    bound = intake_repository.binding(ch)
    feishu_receiver.initialize(config, bound)
    synthetic = (ch, bound)
    enable(config, synthetic)
    old = receive(config, synthetic, identity="old-material")
    before = (root / "a.md").read_bytes()
    try:
        control.start(config, identity, "query", 0)
        control.start(config, identity, "intake", 0)
        wait_until(lambda: control.status(config, identity, "query").get("heartbeat_fresh"))
        control.pause(config, identity, "query", 1)
        wait_until(lambda: not control.status(config, identity, "query").get("process_running"))
        new = receive(config, synthetic, identity="new-material", text="合成保存内容 3.5 MPa")

        def saved():
            with store.readonly(config) as db:
                row = db.execute(
                    "SELECT state FROM channel_intake_records WHERE channel_id=? AND batch_id=?",
                    (identity, new),
                ).fetchone()
                return row and row[0] == "stored"

        wait_until(saved)
        with store.readonly(config) as db:
            assert not db.execute(
                "SELECT 1 FROM channel_intake_records WHERE batch_id=?", (old,)
            ).fetchone()
            assert store.get(db, identity)["value"]["vault_id"] == ch["value"]["vault_id"]
        with database.connect(config.state_root) as db:
            assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM model_calls").fetchone()[0] == 0
        assert (root / "a.md").read_bytes() == before
    finally:
        for role in policy.ROLES:
            p = policy.get(config, identity, role)
            if p and p["enabled"]:
                control.pause(config, identity, role, p["revision"])
        for role in policy.ROLES:
            wait_until(
                lambda role=role: not control.status(config, identity, role).get("process_running")
            )
