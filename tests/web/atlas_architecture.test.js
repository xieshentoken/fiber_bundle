import test from "node:test";
import assert from "node:assert/strict";
import {
  roleObservation,
  receiptObservation,
} from "../../src/fiber_bundle/web/atlas/architecture_model.js";
test("role health requires a fresh running process and keeps independent pause distinct", () => {
  const role = {
    managed: true,
    enabled: true,
    process_running: true,
    heartbeat_fresh: true,
    state: "idle",
  };
  assert.equal(roleObservation(role).tone, "success");
  assert.equal(
    roleObservation({ ...role, heartbeat_fresh: false }).tone,
    "waiting",
  );
  assert.equal(roleObservation({ ...role, enabled: false }).tone, "paused");
  assert.equal(roleObservation(role, true).tone, "waiting");
  assert.equal(roleObservation(undefined).tone, "idle");
  assert.equal(roleObservation({ ...role, state: "unknown" }).tone, "waiting");
});
test("unknown receipts never become green or block an independent query observation", () => {
  assert.equal(
    receiptObservation({
      enabled: true,
      needs_review_batches: 2,
      sending_batches: 0,
    }).tone,
    "waiting",
  );
  assert.equal(
    receiptObservation({ enabled: false, needs_review_batches: 0 }).tone,
    "paused",
  );
  assert.equal(
    receiptObservation({ enabled: true, pending_batches: 1 }, true).tone,
    "waiting",
  );
  assert.equal(receiptObservation(null).tone, "idle");
});
