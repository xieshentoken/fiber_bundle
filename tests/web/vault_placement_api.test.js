import test from "node:test";
import assert from "node:assert/strict";
import {
  createVaultPlacementApi,
  vaultPlacementErrorText,
} from "../../src/fiber_bundle/web/vault_placement_api.js";

test("placement actions carry scope and explicit confirmation and encode identities", async () => {
  const calls = [],
    api = createVaultPlacementApi(async (...args) => {
      calls.push(args);
      return {};
    });
  const scope = { client_id: "client", vault_id: "vault" };
  await api.list("a b", "x/y");
  await api.preview({
    ...scope,
    note_identity: "publication:test",
    expected_sha256: "a".repeat(64),
  });
  await api.action("x/y", "reconcile", scope);
  await api.legacy("a b", "x/y");
  await api.migrationPreview({
    ...scope,
    kind: "article",
    publication_id: "test",
  });
  await api.migrationAction("x/y", "apply", scope);
  assert.equal(
    calls[0][0],
    "/admin/vault-placements?client_id=a%20b&vault_id=x%2Fy",
  );
  assert.equal(calls[2][0], "/admin/vault-placements/x%2Fy/reconcile");
  assert.deepEqual(calls[2][2], { ...scope, confirm: true });
  assert.equal(
    calls[3][0],
    "/admin/vault-layout-migrations?client_id=a%20b&vault_id=x%2Fy",
  );
  assert.deepEqual(calls[5][2], { ...scope, confirm: true });
});

test("previews reject malformed readiness and link lists", async () => {
  const api = createVaultPlacementApi(
    async (path, method, body, validator) => validator,
  );
  const validate = await api.preview({});
  assert.equal(
    validate({ operation_id: "id", ready: true, unresolved_links: [] }),
    true,
  );
  assert.equal(
    validate({ operation_id: "id", ready: "true", unresolved_links: [] }),
    false,
  );
  assert.equal(
    validate({ operation_id: "id", ready: true, unresolved_links: {} }),
    false,
  );
  assert.match(vaultPlacementErrorText("placement_note_changed"), /重新/);
});

test("external repair scan and batch actions preserve scope and require confirmation", async () => {
  const calls = [],
    api = createVaultPlacementApi(async (...args) => {
      calls.push(args);
      return {};
    });
  const scope = { client_id: "client one", vault_id: "vault/two" };
  await api.scanExternal(scope);
  await api.externalPreview({ ...scope, items: [] });
  await api.externalRead("batch/id", scope.client_id, scope.vault_id);
  await api.externalApply("batch/id", scope);
  assert.equal(calls[0][0], "/admin/vault-publication-rebinds/scan");
  assert.equal(calls[1][0], "/admin/vault-publication-rebinds/batch/preview");
  assert.equal(
    calls[2][0],
    "/admin/vault-publication-rebinds/batch/batch%2Fid?client_id=client%20one&vault_id=vault%2Ftwo",
  );
  assert.equal(
    calls[3][0],
    "/admin/vault-publication-rebinds/batch/batch%2Fid/apply",
  );
  assert.deepEqual(calls[3][2], { ...scope, confirm: true, max_items: 24 });
});
