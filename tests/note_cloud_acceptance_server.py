"""Isolated, loopback-only note-cloud fixture; never reads a real Fiber state root.

Run from the candidate project with PYTHONPATH=src and its compatible Python:
    python tests/note_cloud_acceptance_server.py --port 8877

The generated access file is mode 0600. Passwords and capability tokens are never
printed. Each run creates a new fixture directory and leaves old results intact.
The real application is served with background lifespans disabled: fixture reads
exercise the real HTTP permissions/index/cloud paths without starting workers.
"""

import argparse
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import tempfile
import time
from pathlib import Path
from uuid import uuid4


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


class LoopbackOnly:
    """Deny outbound DNS/connect/datagrams before importing application modules."""

    def __init__(self, audit_path: Path) -> None:
        self.audit_path = audit_path
        previous = json.loads(audit_path.read_text()) if audit_path.exists() else {}
        self.attempts = int(previous.get("blocked_non_loopback_attempts", 0))
        self.started = time.time()
        self.write()

    def write(self) -> None:
        write_json(
            self.audit_path,
            {
                "schema": "fiber-note-cloud-fixture-network/v1",
                "started_at": self.started,
                "blocked_non_loopback_attempts": self.attempts,
                "background_lifespans_enabled": False,
                "allowed_network": "127.0.0.0/8 only; Unix sockets remain local",
            },
        )

    def check(self, address: object) -> None:
        if not isinstance(address, tuple):
            return  # Unix sockets do not cross a network boundary.
        host = str(address[0])
        try:
            allowed = ipaddress.ip_address(host) in ipaddress.ip_network("127.0.0.0/8")
        except ValueError:
            allowed = False
        if not allowed:
            self.attempts += 1
            self.write()
            raise OSError("Synthetic acceptance fixture blocks non-loopback network")

    def install(self) -> None:
        connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
        sendto, getaddrinfo = socket.socket.sendto, socket.getaddrinfo

        def guarded_connect(sock: socket.socket, address: object) -> object:
            self.check(address)
            return connect(sock, address)

        def guarded_connect_ex(sock: socket.socket, address: object) -> int:
            self.check(address)
            return connect_ex(sock, address)

        def guarded_sendto(sock: socket.socket, data: bytes, *args: object) -> int:
            self.check(args[-1])
            return sendto(sock, data, *args)

        def guarded_getaddrinfo(
            host: object, port: object, *args: object, **kwargs: object
        ) -> list:
            if host is not None:
                self.check((host, port))
            return getaddrinfo(host, port, *args, **kwargs)

        socket.socket.connect = guarded_connect
        socket.socket.connect_ex = guarded_connect_ex
        socket.socket.sendto = guarded_sendto
        socket.getaddrinfo = guarded_getaddrinfo


def write_note(
    root: Path, relative: str, title: str, links: list[str], *, moc: bool = False
) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "---\ntype: moc\n---\n" if moc else ""
    body = "\n".join(f"[[{link}]]" for link in links)
    path.write_text(
        f"{header}# {title}\n\n合成验收资料 / Synthetic local fixture.\n\n{body}\n",
        encoding="utf-8",
    )


def small_library(root: Path) -> list[str]:
    samples = [
        ("材料/界面粘附.md", "界面粘附 · Adhesion Science", ["材料/表面能", "材料/聚合物"]),
        ("材料/表面能.md", "表面能与润湿", ["材料/接触角", "材料/界面粘附"]),
        ("材料/聚合物.md", "Polymer interfaces", ["材料/复合材料"]),
        ("材料/接触角.md", "Contact angle 测量", ["材料/界面粘附"]),
        ("材料/复合材料.md", "复合材料试验", ["工程/热传导"]),
        ("工程/热传导.md", "热传导 / Heat transfer", ["工程/热阻", "工程/热边界"]),
        ("工程/热阻.md", "Thermal resistance", ["工程/换热器"]),
        ("工程/热边界.md", "热边界条件", ["工程/热传导"]),
        ("工程/换热器.md", "换热器设计", ["工程/热边界", "方法/不确定度"]),
        ("方法/不确定度.md", "测量不确定度", ["方法/实验记录"]),
        ("方法/实验记录.md", "实验记录 / Lab notebook", ["材料/接触角"]),
        ("方法/文献阅读.md", "Literature reading", ["方法/实验记录"]),
        ("Inbox/孤立中文笔记.md", "孤立笔记：尚未建立关联", []),
        ("Inbox/An isolated English note.md", "An isolated English note", []),
        (
            "Inbox/很长的标题.md",
            "一个用于验证悬停完整标题、搜索和窄屏布局的很长中文笔记标题 / A complete long title",
            [],
        ),
    ]
    for relative, title, links in samples:
        write_note(root, relative, title, links)
    write_note(
        root, "Map/材料科学 MOC.md", "材料科学 MOC", [s[0][:-3] for s in samples[:5]], moc=True
    )
    write_note(root, "Map/热管理 MOC.md", "热管理 MOC", [s[0][:-3] for s in samples[5:9]], moc=True)
    write_note(
        root, "Map/研究方法 MOC.md", "研究方法 MOC", [s[0][:-3] for s in samples[9:12]], moc=True
    )
    return [s[0] for s in samples]


def dense_library(root: Path, total: int = 2500) -> None:
    clusters, per_cluster = 6, 410
    titles = ["材料界面", "热管理", "流体计算", "实验方法", "文献网络", "工程设计"]
    for cluster in range(clusters):
        directory = f"Cluster-{cluster + 1}"
        for index in range(per_cluster):
            # Every edge is a literal link in this generated source. Ring, hub,
            # local chords and sparse inter-cluster links produce real clusters.
            targets = {index // 13, (index + 1) % per_cluster, (index + 7) % per_cluster}
            targets.discard(index)
            links = [f"{directory}/note-{target:04d}" for target in sorted(targets)]
            if index == 13:
                links.append(f"Cluster-{(cluster + 1) % clusters + 1}/note-0013")
            write_note(
                root,
                f"{directory}/note-{index:04d}.md",
                f"{titles[cluster]} · Note {index:04d}",
                links,
            )
        write_note(
            root,
            f"Map/{titles[cluster]} MOC.md",
            f"{titles[cluster]} MOC",
            [f"{directory}/note-{index:04d}" for index in range(0, per_cluster, 13)],
            moc=True,
        )
    for index in range(total - clusters * (per_cluster + 1)):
        write_note(
            root,
            f"Isolates/isolated-{index:03d}.md",
            f"独立观察 / Isolated observation {index}",
            [],
        )


def manual_membership(config: object, client: dict, vault: str, root: Path) -> dict:
    from fiber_bundle import moc_associations, moc_files, note_identities

    relative = "材料/界面粘附.md"
    sha = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    authorization = "Bearer " + client["token"]
    note = note_identities.run(config, authorization, vault, "track", relative, sha)
    mid = str(uuid4())
    moc_associations.run(
        config, authorization, vault, "create", mid, title="人工确认材料 MOC", revision=0
    )
    moc_associations.run(
        config,
        authorization,
        vault,
        "link",
        mid,
        revision=1,
        owner="manual",
        note_identity=note["note_identity"],
        expected_sha256=sha,
    )
    path = "Map/人工确认材料 MOC.md"
    planned = moc_files.run(
        config,
        authorization,
        vault,
        "plan-file",
        mid,
        revision=2,
        relative=path,
        expected_sha256="absent",
    )
    result = moc_files.run(
        config, authorization, vault, "apply-file", mid, plan_id=planned["plan_id"]
    )
    assert result["moc_file_write_performed"]
    return {"moc_id": mid, "path": path, "note_path": relative, "owner": "manual"}


def prepare(acceptance: Path, port: int) -> tuple[object, dict]:
    from note_cloud_complete_fixture import (
        confirmed_missing_intake,
        dense_cases,
        save_members,
        small_cases,
        summarize,
        validate_complete,
    )

    from fiber_bundle import auth, database, moc_store, note_search_build
    from fiber_bundle.config import load_config
    from fiber_bundle.knowledge_graph import cloud

    root = Path(tempfile.mkdtemp(prefix="note-cloud-fixture-", dir=acceptance)).resolve()
    config_path = root / "config.json"
    write_json(
        config_path,
        {
            "state_root": ".state",
            "vaults": {
                "personal": "test vault/temp_vault1",
                "supplier": "test vault/temp_vault2",
            },
        },
    )
    config = load_config(config_path, root)
    database.initialize(config)
    with database.connect(config.state_root) as db:
        client = auth.create_client(db, "Note Cloud Synthetic Acceptance", False)
        vaults = {row["name"]: row["id"] for row in db.execute("SELECT id,name FROM vaults")}
        for vault in vaults.values():
            auth.set_grant(db, client["id"], vault, query=True, original=True, add=True)
    small = root / "test vault/temp_vault1"
    dense = root / "test vault/temp_vault2"
    small_library(small)
    membership = manual_membership(config, client, vaults["personal"], small)
    small_complete = small_cases(config, client, vaults["personal"], small)
    confirmed = confirmed_missing_intake(
        config,
        client,
        vaults["personal"],
        small,
    )
    ownership_mid = small_complete["mocs"]["ownership"]["id"]
    current = moc_store.load(config, vaults["personal"], ownership_mid)
    save_members(
        config,
        client,
        vaults["personal"],
        ownership_mid,
        {**current.members, confirmed["identity"]: ["manual"]},
    )
    small_complete["confirmed_missing_intake"] = confirmed
    small_complete["cases"]["confirmed_publication"] = confirmed["identity"]
    dense_library(dense)
    dense_complete = dense_cases(config, client, vaults["supplier"], dense)
    password = secrets.token_urlsafe(24)
    auth.set_password(config, password)
    builds = {}
    for name, vault in vaults.items():
        builds[name] = note_search_build.build(config, client["id"], vault)
    origin = f"http://127.0.0.1:{port}"
    access = {
        "origin": origin,
        "url": origin + "/#/note-cloud",
        "password": password,
        "client_id": client["id"],
        "vaults": vaults,
        "config_path": str(config_path),
        "fixture_root": str(root),
        "synthetic_only": True,
    }
    write_json(root / "fixture-access.json", access)

    def sealed_files() -> dict:
        return {
            str(path.relative_to(root)): (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_ino,
                path.stat().st_mtime_ns,
            )
            for path in root.rglob("*")
            if path.is_file() and not path.name.endswith(("-wal", "-shm"))
        }

    sealed = sealed_files()
    summaries = {}
    checks = {}
    for name, vault in vaults.items():
        with database.connect(config.state_root) as db:
            value = cloud.read(config, db, client["id"], vault)
            checks[name] = validate_complete(
                value,
                small_complete if name == "personal" else dense_complete,
                name,
            )
            for query in ("PRIVATE-TITLE-SENTINEL", "PRIVATE TITLE SENTINEL"):
                assert not cloud.search(config, db, client["id"], vault, query)["items"]
        summaries[name] = {"vault_id": vault, **summarize(value)}
    assert sealed == sealed_files(), "Snapshot/search changed synthetic fixture files"
    from fiber_bundle.organization import store as organization_store

    with database.connect(config.state_root) as db:
        model_calls = db.execute("SELECT count(*) FROM model_calls").fetchone()[0]
    model_reviews = len(organization_store.rows(config, "model_reviews"))
    assert model_calls == 0 and model_reviews == 0
    manifest = {
        "schema": "fiber-note-cloud-fixture/v2",
        "origin": origin,
        "fixture_root": str(root),
        "libraries": summaries,
        "manual_membership": membership,
        "complete_cases": {"personal": small_complete, "supplier": dense_complete},
        "verified_cases": checks,
        "index_builds": builds,
        "background_lifespans_enabled": False,
        "native_draft_seed": "already_compiled_synthetic_metadata_only",
        "native_runtime_executed": False,
        "read_only_verified_files": len(sealed),
        "model_ledger": {"model_calls": model_calls, "model_reviews": model_reviews},
        "source_root": str(Path(__file__).resolve().parents[1]),
    }
    write_json(acceptance / "fixture-manifest.json", manifest)
    write_json(acceptance / "fixture-access.json", access)
    print(
        f"Synthetic note-cloud fixture prepared at {origin}; credentials saved locally.", flush=True
    )
    return config, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument(
        "--acceptance-dir", type=Path, default=Path(__file__).resolve().parents[2] / "acceptance"
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--reuse-fixture", action="store_true")
    arguments = parser.parse_args()
    if arguments.port in {8765, 8766} or not 1024 <= arguments.port <= 65535:
        parser.error("use an unprivileged isolated port other than 8765/8766")
    acceptance = arguments.acceptance_dir.resolve()
    acceptance.mkdir(parents=True, exist_ok=True)
    guard = LoopbackOnly(acceptance / "fixture-network-audit.json")
    guard.install()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", arguments.port))
    try:
        if arguments.reuse_fixture:
            from fiber_bundle.config import load_config

            access = json.loads((acceptance / "fixture-access.json").read_text())
            fixture_root = Path(access["fixture_root"]).resolve(strict=True)
            if (
                access.get("synthetic_only") is not True
                or fixture_root.parent != acceptance
                or not fixture_root.name.startswith("note-cloud-fixture-")
                or access.get("origin") != f"http://127.0.0.1:{arguments.port}"
            ):
                raise ValueError("Only this local synthetic fixture can be reused")
            config = load_config(fixture_root / "config.json", fixture_root)
            manifest = json.loads((acceptance / "fixture-manifest.json").read_text())
            if manifest.get("fixture_root") != str(fixture_root):
                raise ValueError("Fixture manifest does not match the local fixture")
        else:
            config, manifest = prepare(acceptance, arguments.port)
        if arguments.prepare_only:
            return
        import uvicorn
        from fastapi import Request
        from fastapi.responses import JSONResponse

        from fiber_bundle import auth, database
        from fiber_bundle.api import create_app
        from fiber_bundle.http_guard import Settings

        app = create_app(config, Settings(port=arguments.port, session_ttl=86400))

        @app.get("/__fixture/manifest")
        def fixture_manifest(request: Request) -> JSONResponse:
            with database.connect(config.state_root) as db:
                auth.admin(db, request.cookies.get(auth.COOKIE, ""))
            return JSONResponse(manifest)

        @app.get("/__fixture/network-audit")
        def fixture_audit(request: Request) -> JSONResponse:
            with database.connect(config.state_root) as db:
                auth.admin(db, request.cookies.get(auth.COOKIE, ""))
            guard.write()
            return JSONResponse(json.loads(guard.audit_path.read_text()))

        uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=arguments.port,
                lifespan="off",
                access_log=False,
                log_level="info",
            )
        ).run(sockets=[listener])
    finally:
        listener.close()
        guard.write()


if __name__ == "__main__":
    main()
