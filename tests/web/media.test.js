import test from "node:test";
import assert from "node:assert/strict";
import { assessment } from "../../src/fiber_bundle/web/media.js";
import { createAdminApi } from "../../src/fiber_bundle/web/api.js";
const packet = {
  mode: "audio_only",
  candidate: {
    source_sha256: "a".repeat(64),
    result_sha256: "b".repeat(64),
    glossary_sha256: "c".repeat(64),
    glossary_revision: 1,
    duration_ms: 3000,
  },
  units: [{ unit_id: "S1" }],
};
const decisions = [
  { unit_id: "S1", verdict: "uncertain", reason: "Actual unresolved evidence" },
];
test("media assessment binds source, result, revision and explicit full duration review", () => {
  for (const args of [
    [false, true, false, decisions],
    [true, false, false, decisions],
    [true, true, true, decisions],
    [true, true, false, []],
    [true, true, false, [{ ...decisions[0], reason: " " }]],
  ])
    assert.throws(() => assessment(packet, ...args));
  const value = assessment(packet, true, true, false, decisions);
  assert.equal(value.source_sha256, packet.candidate.source_sha256);
  assert.equal(value.result_sha256, packet.candidate.result_sha256);
  assert.equal(value.glossary_revision, 1);
  assert.deepEqual(value.coverage_ranges, [{ start_ms: 0, end_ms: 3000 }]);
  assert.equal(value.judgments[0].verdict, "uncertain");
  assert.throws(() =>
    assessment(
      { ...packet, mode: "full_evidence" },
      true,
      true,
      false,
      decisions,
    ),
  );
});
test("media review, publication and SHA confirmation are distinct CSRF requests", async () => {
  const calls = [];
  const api = createAdminApi(
    { revision: 1, csrf: "synthetic" },
    { onConnection() {}, onUnauthorized() {} },
    async (path, options) => {
      calls.push({ path, options });
      return { ok: true, json: async () => ({ state: "ready" }) };
    },
  );
  await api.reviewMedia(
    "a/b",
    assessment(packet, true, true, false, decisions),
  );
  await api.publishMedia("a/b", { review_id: "synthetic-review" });
  await api.mediaCleanupPlan("a/b");
  await api.mediaCleanup("a/b", { confirmation_sha256: "d".repeat(64) });
  assert.deepEqual(
    calls.map((c) => c.path),
    [
      "/admin/media/a%2Fb/review",
      "/admin/media/a%2Fb/publish",
      "/admin/media/a%2Fb/cleanup-plan",
      "/admin/media/a%2Fb/cleanup",
    ],
  );
  assert.ok(
    calls.every((c) => c.options.headers["X-CSRF-Token"] === "synthetic"),
  );
  assert.equal(JSON.parse(calls[1].options.body).review_id, "synthetic-review");
  assert.equal(
    JSON.parse(calls[3].options.body).confirmation_sha256,
    "d".repeat(64),
  );
});
