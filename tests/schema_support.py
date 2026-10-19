"""Construct historical fixtures without silently retaining future schema objects."""

import sqlite3

GRAPH_TRIGGERS = {
    "graph_grant_insert",
    "graph_grant_update",
    "graph_grant_delete",
    "graph_client_active",
    "graph_vault_active",
}

PROCESSING_TABLES = (
    "model_configuration_calls",
    "model_configurations",
    "model_credentials",
    "model_configuration_settings",
    "video_tasks",
    "pi_processing_runs",
    "model_call_budget_bindings",
    "model_budget_reservations",
    "model_credential_aliases",
    "model_budget_policies",
)


def remove_automatic_schema(db: sqlite3.Connection) -> None:
    """Restore the real schema30 publication contracts before historical migration tests."""
    for name in PROCESSING_TABLES:
        db.execute("DROP TABLE IF EXISTS " + name)
    if "system_review_id" in {r[1] for r in db.execute("PRAGMA table_info(document_publications)")}:
        from fiber_bundle.material_schema import SCHEMA_V9

        rows = db.execute(
            "SELECT run_id,review_id,source_sha256,result_sha256,relative_path"
            ",staging_path,manifest_json,state,created_at FROM document_public"
            "ations WHERE review_id IS NOT NULL"
        ).fetchall()
        db.execute("DROP TABLE document_publications")
        db.execute(
            "CREATE TABLE document_publications"
            + SCHEMA_V9.split("CREATE TABLE document_publications")[1].strip().rstrip(";")
        )
        db.executemany("INSERT INTO document_publications VALUES(?,?,?,?,?,?,?,?,?)", rows)
    if "system_review_id" in {r[1] for r in db.execute("PRAGMA table_info(media_publications)")}:
        from fiber_bundle.media_processing_schema import SCHEMA_V12

        rows = db.execute(
            "SELECT job_id,review_id,relative_path,staging_path,manifest_json,"
            "state,created_at FROM media_publications WHERE review_id IS NOT N"
            "ULL"
        ).fetchall()
        db.execute("DROP TABLE media_publications")
        db.execute(
            "CREATE TABLE media_publications"
            + SCHEMA_V12.split("CREATE TABLE media_publications")[1].split(";")[0]
        )
        db.executemany("INSERT INTO media_publications VALUES(?,?,?,?,?,?,?)", rows)
    for name in ("automatic_reviews", "automatic_jobs", "automatic_policies"):
        db.execute("DROP TABLE IF EXISTS " + name)


def remove_agent_schema(db: sqlite3.Connection) -> None:
    """Remove schema27+ additions when constructing a genuine historical fixture."""
    remove_automatic_schema(db)
    db.execute("DROP TABLE IF EXISTS vault_directory_previews")
    db.execute("DROP TABLE IF EXISTS publication_locations")
    db.execute("DROP TABLE IF EXISTS vault_save_policies")
    db.execute("DROP TABLE IF EXISTS model_call_credentials")
    db.execute("DROP TABLE IF EXISTS system_settings")
    db.execute("DROP TABLE IF EXISTS agent_connection_operations")
    db.execute("DROP TABLE IF EXISTS agent_connections")


def remove_vault_catalog_schema(db: sqlite3.Connection) -> None:
    remove_agent_schema(db)
    columns = {r[1] for r in db.execute("PRAGMA table_info(vaults)")}
    for name in ("absolute_root", "root_kind", "read_only", "attachment_state", "scope_json"):
        if name in columns:
            db.execute("ALTER TABLE vaults DROP COLUMN " + name)
    db.execute("DROP TABLE IF EXISTS vault_admissions")
    db.execute("DROP TABLE IF EXISTS vault_operations")


def remove_graph_epoch_schema(db: sqlite3.Connection) -> None:
    remove_vault_catalog_schema(db)
    # Restore the actual pre-23/24 contracts rather than merely lowering user_version.
    from fiber_bundle.document_schema import SCHEMA_V8
    from fiber_bundle.source_schema import SCHEMA_V20

    foreign_keys = db.execute("PRAGMA foreign_keys").fetchone()[0]
    db.execute("PRAGMA foreign_keys=OFF")
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='document_tasks'").fetchone():
            rows = db.execute("SELECT * FROM document_tasks").fetchall()
            db.execute("DROP TABLE document_tasks")
            db.executescript(SCHEMA_V8)
            if rows:
                db.executemany(
                    "INSERT INTO document_tasks VALUES(" + ",".join("?" for _ in rows[0]) + ")",
                    rows,
                )
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='document_profiles'").fetchone():
            db.executescript(SCHEMA_V20.split("CREATE TABLE IF NOT EXISTS source_task_bindings")[0])
    finally:
        db.execute(f"PRAGMA foreign_keys={foreign_keys}")
    # Fixtures below schema 21 also exclude the newer GUI upload ledger.
    for name in ("source_expert_publications", "source_expert_attempts", "source_expert_tasks"):
        db.execute(f"DROP TABLE IF EXISTS {name}")
    db.execute("DROP TABLE IF EXISTS source_import_chunks")
    db.execute("DROP TABLE IF EXISTS source_imports")
    for name in sorted(GRAPH_TRIGGERS):
        db.execute(f'DROP TRIGGER IF EXISTS "{name}"')
    db.execute("DROP TABLE IF EXISTS graph_permission_epochs")


def assert_preserved_rows(db: sqlite3.Connection, table: str, before: list) -> None:
    actual = [tuple(r) for r in db.execute('SELECT * FROM "' + table + '" ORDER BY rowid')]
    expected = [tuple(r) for r in before]
    if table == "vaults":
        assert all(
            r[-5:] == (None, "legacy", 0, "attached", '{"include":[""],"exclude":[]}')
            for r in actual
        )
        actual = [r[:-5] for r in actual]
    assert actual == expected


def assert_graph_epoch_seed(db: sqlite3.Connection, table_rows: dict | None = None) -> None:
    expected = [
        (row[0], row[1], 1)
        for row in db.execute("SELECT client_id,vault_id FROM client_vault_grants ORDER BY 1,2")
    ]
    actual = [
        tuple(row) for row in db.execute("SELECT * FROM graph_permission_epochs ORDER BY 1,2")
    ]
    assert actual == expected
    assert {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'graph_%'"
        )
    } == GRAPH_TRIGGERS
    if table_rows is not None:
        for name in PROCESSING_TABLES:
            assert table_rows.pop(name) == []
        for name in ("automatic_reviews", "automatic_jobs", "automatic_policies"):
            assert table_rows.pop(name) == []
        assert table_rows.pop("vault_directory_previews") == []
        assert table_rows.pop("vault_save_policies") == []
        assert table_rows.pop("publication_locations") == []
        assert table_rows.pop("system_settings") == []
        assert table_rows.pop("model_call_credentials") == []
        assert table_rows.pop("agent_connections") == []
        assert table_rows.pop("agent_connection_operations") == []
        assert table_rows.pop("vault_admissions") == []
        assert table_rows.pop("vault_operations") == []
        assert all(
            r[-5:] == (None, "legacy", 0, "attached", '{"include":[""],"exclude":[]}')
            for r in table_rows["vaults"]
        )
        table_rows["vaults"] = [r[:-5] for r in table_rows["vaults"]]
        assert sorted(table_rows.pop("graph_permission_epochs")) == expected
        for name in (
            "source_imports",
            "source_import_chunks",
            "source_expert_tasks",
            "source_expert_attempts",
            "source_expert_publications",
        ):
            assert table_rows.pop(name) == []
