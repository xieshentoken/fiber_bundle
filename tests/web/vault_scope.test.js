import test from "node:test";
import assert from "node:assert/strict";
import { selectableVaults } from "../../src/fiber_bundle/web/vault_scope.js";
test("page-local selectors expose active attached libraries and retain read-only inspection", () => {
  const records = [
    { id: "active", status: "active", attachment_state: "attached" },
    { id: "readonly", status: "active", read_only: true },
    { id: "detached", status: "active", attachment_state: "detached" },
    { id: "trashed", status: "trashed", attachment_state: "trashed" },
    { id: "disabled", status: "disabled" },
  ];
  assert.deepEqual(
    selectableVaults(records).map((v) => v.id),
    ["active", "readonly"],
  );
  assert.equal(records.length, 5);
});
