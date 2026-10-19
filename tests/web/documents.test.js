import test from "node:test";
import assert from "node:assert/strict";
import { assessment } from "../../src/fiber_bundle/web/documents.js";
import { createAdminApi } from "../../src/fiber_bundle/web/api.js";

const packet = {
  source_sha256: "a".repeat(64),
  result_sha256: "b".repeat(64),
  units: [{ unit_id: "u1" }],
};
const decisions = [
  { unit_id: "u1", verdict: "uncertain", reason: "Actual unresolved unit" },
];
test("review requires explicit original and visual confirmations, every unit and reason", () => {
  assert.throws(() => assessment(packet, false, true, decisions));
  assert.throws(() => assessment(packet, true, false, decisions));
  assert.throws(() => assessment(packet, true, true, []));
  assert.throws(() =>
    assessment(packet, true, true, [{ ...decisions[0], reason: " " }]),
  );
  assert.deepEqual(assessment(packet, true, true, decisions), {
    source_sha256: packet.source_sha256,
    result_sha256: packet.result_sha256,
    source_reviewed: true,
    visual_reviewed: true,
    judgments: decisions,
  });
});
test("review and publication are distinct CSRF-protected requests; task path is encoded", async () => {
  const calls = [];
  const api = createAdminApi(
    { revision: 1, csrf: "synthetic" },
    { onConnection() {}, onUnauthorized() {} },
    async (path, options) => {
      calls.push({ path, options });
      return { ok: true, json: async () => ({ state: "ready" }) };
    },
  );
  await api.reviewDocument("a/b", assessment(packet, true, true, decisions));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/admin/documents/a%2Fb/reviews");
  assert.equal(calls[0].options.headers["X-CSRF-Token"], "synthetic");
  await api.publishDocument("a/b");
  assert.equal(calls[1].path, "/admin/documents/a%2Fb/publish");
  assert.equal(calls[1].options.body, undefined);
});

test("safe split pauses explain retained candidate with bounded public errors", async () => {
  for (const code of [
    "document_split_unit_limit",
    "document_split_total_limit",
    "document_split_part_limit",
    "document_split_reference_scope",
  ]) {
    const api = createAdminApi(
      { revision: 1, csrf: "synthetic" },
      { onConnection() {}, onUnauthorized() {} },
      async () => ({
        ok: false,
        status: 409,
        json: async () => ({ error: code }),
      }),
    );
    await assert.rejects(api.publishDocument("synthetic"), /候选已保留/);
  }
});
