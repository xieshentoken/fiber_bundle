"""Candidate schema 21: durable authorization generations, independent of derived graphs."""

SCHEMA_V21 = (
    """CREATE TABLE graph_permission_epochs (
    client_id TEXT NOT NULL REFERENCES clients(id), vault_id TEXT NOT NULL REFERENCES vaults(id),
    revision INTEGER NOT NULL CHECK(revision > 0), PRIMARY KEY(client_id,vault_id)
    ) STRICT""",
    "INSERT INTO graph_permission_epochs SELECT client_id,vault_id,1 FROM client_vault_grants",
    """CREATE TRIGGER graph_grant_insert AFTER INSERT ON client_vault_grants BEGIN
    INSERT INTO graph_permission_epochs VALUES(NEW.client_id,NEW.vault_id,1)
    ON CONFLICT(client_id,vault_id) DO UPDATE SET revision=revision+1; END""",
    """CREATE TRIGGER graph_grant_update AFTER UPDATE ON client_vault_grants
    WHEN OLD.can_query != NEW.can_query OR OLD.can_original != NEW.can_original
    OR OLD.can_add != NEW.can_add BEGIN
    UPDATE graph_permission_epochs SET revision=revision+1
    WHERE client_id=NEW.client_id AND vault_id=NEW.vault_id; END""",
    """CREATE TRIGGER graph_grant_delete AFTER DELETE ON client_vault_grants BEGIN
    UPDATE graph_permission_epochs SET revision=revision+1
    WHERE client_id=OLD.client_id AND vault_id=OLD.vault_id; END""",
    """CREATE TRIGGER graph_client_active AFTER UPDATE OF active ON clients
    WHEN OLD.active != NEW.active BEGIN
    UPDATE graph_permission_epochs SET revision=revision+1 WHERE client_id=NEW.id; END""",
    """CREATE TRIGGER graph_vault_active AFTER UPDATE OF status ON vaults
    WHEN OLD.status != NEW.status BEGIN
    UPDATE graph_permission_epochs SET revision=revision+1 WHERE vault_id=NEW.id; END""",
)
