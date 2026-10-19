import test from "node:test";
import assert from "node:assert/strict";
import { createVaultMergeApi } from "../../src/fiber_bundle/web/vault_merge_api.js";

test("merge freeze/start/recovery carry explicit frozen hashes and encode identities", async () => {
  const calls = [],
    api = createVaultMergeApi(async (...args) => {
      calls.push(args);
      return { id: "one", state: "prepared" };
    });
  await api.freeze({ target_vault_id: "target" }, "frozen-sha");
  await api.start("op/id", {
    plan_sha256: "frozen-sha",
    backup_password: "synthetic-only",
  });
  await api.action("op/id", "rollback", "frozen-sha");
  assert.deepEqual(calls[0].slice(0, 3), [
    "/admin/vault-merges/freeze",
    "POST",
    { selection: { target_vault_id: "target" }, plan_sha256: "frozen-sha" },
  ]);
  assert.equal(calls[1][0], "/admin/vault-merges/op%2Fid/start");
  assert.deepEqual(calls[2].slice(0, 3), [
    "/admin/vault-merges/op%2Fid/rollback",
    "POST",
    { plan_sha256: "frozen-sha" },
  ]);
  assert.throws(() => api.action("op", "arbitrary", "hash"));
});
