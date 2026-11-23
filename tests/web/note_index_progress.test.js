import test from "node:test";
import assert from "node:assert/strict";
test("queued and partial index updates never claim completion in either language", async () => {
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
  const { noteIndexOutcome, indexProblem, indexIsRunning } =
    await import("../../src/fiber_bundle/web/note_index_errors.js");
  const { setLocale } =
    await import("../../src/fiber_bundle/web/i18n/index.js");
  for (const lang of ["zh-CN", "en"]) {
    setLocale(lang);
    const queued = noteIndexOutcome({ state: "pending", job_id: "one" });
    assert.doesNotMatch(queued, /更新完成|Index updated\./);
    assert.doesNotMatch(
      noteIndexOutcome({ state: "partial" }),
      /更新完成|Index updated\./,
    );
    assert.match(
      noteIndexOutcome({ state: "ready" }),
      /更新完成|Index updated\./,
    );
    assert.ok(
      indexProblem("vault_sync_cloud_metadata_unavailable").includes("iCloud"),
    );
  }
  setLocale("zh-CN");
  assert.equal(
    indexIsRunning({
      state: "partial",
      sync: {},
      update: { state: "building" },
    }),
    false,
  );
  assert.equal(indexIsRunning({ state: "pending", sync: {} }), true);
  assert.equal(indexIsRunning({ state: "waiting", sync: {} }), false);
});
