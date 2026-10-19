import test from "node:test";
import assert from "node:assert/strict";
import {
  createVaultRelocationApi,
  relocationActive,
  relocationErrorText,
  relocationStatus,
} from "../../src/fiber_bundle/web/vault_relocation_api.js";

test("location changes carry frozen hashes and a strict external-writer acknowledgement", async () => {
  const calls = [],
    api = createVaultRelocationApi(async (...args) => {
      calls.push(args);
      return {};
    });
  await api.preview("vault/a", "/synthetic/new");
  await api.start("operation/a", "frozen");
  await api.recover("operation/a", "restore", "fresh-recovery-proof");
  assert.deepEqual(calls[0].slice(0, 3), [
    "/admin/vault-relocations/preview",
    "POST",
    { vault_id: "vault/a", destination: "/synthetic/new" },
  ]);
  assert.equal(calls[1][0], "/admin/vault-relocations/operation%2Fa/start");
  assert.deepEqual(calls[1][2], {
    plan_sha256: "frozen",
    external_writers_stopped: true,
  });
  assert.deepEqual(calls[2][2], {
    action: "restore",
    recovery_sha256: "fresh-recovery-proof",
  });
  assert.throws(() => api.recover("id", "delete", "sha"));
});

test("incomplete and recovery states remain blocked, completed states do not", () => {
  for (const state of [
    "quiescing",
    "checkpointing",
    "moving",
    "switching",
    "verifying",
    "needs_recovery",
    "restoring",
  ])
    assert.equal(relocationActive({ state }), true);
  for (const state of ["prepared", "complete", "restored", "cancelled"])
    assert.equal(relocationActive({ state }), false);
  assert.equal(relocationStatus({ id: "id", state: "complete" }), false);
  assert.match(relocationErrorText("relocation_target_exists"), /不会覆盖/);
  assert.equal(relocationErrorText("private-unbounded-error"), null);
});
