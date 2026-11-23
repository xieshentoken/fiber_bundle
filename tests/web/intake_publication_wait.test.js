import test from "node:test";
import assert from "node:assert/strict";
import { automaticStatus } from "../../src/fiber_bundle/web/automatic_status.js";
import { createVaultSaveApi } from "../../src/fiber_bundle/web/vault_save_api.js";

test("publication wait displays the owning archive dependency", () => {
  const status = automaticStatus({
    state: "waiting",
    display_state: "waiting_publication",
  });
  assert.equal(status.text, "等待归档收尾");
  assert.equal(status.tone, "waiting");
  assert.equal(status.actionRequired, true);
});

test("cached recovery explicitly excludes new model and follow-up work", async () => {
  const calls = [];
  const api = createVaultSaveApi(async (...args) => {
    calls.push(args);
    return {};
  });
  await api.reconcileAutomaticJob("vault/a", "job/b", false, true);
  assert.equal(
    calls[0][0],
    "/admin/vault-catalog/vault%2Fa/automatic-jobs/job%2Fb/reconcile",
  );
  assert.deepEqual(calls[0][2], {
    approve_new_model_attempt: false,
    publication_only: true,
  });
});
