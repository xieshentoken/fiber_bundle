import test from "node:test";
import assert from "node:assert/strict";
globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { workspaceState } =
  await import("../../src/fiber_bundle/web/organization_workspace.js");

test("preparing a new task moves completed results to history and survives refresh", () => {
  const jobs = [
    {
      id: "old",
      state: "completed",
      items: [{ path: "Resource/archived.md" }],
    },
  ];
  const original = structuredClone(jobs);
  const draft = { preparingFor: "old", mode: "quick" };
  for (let refresh = 0; refresh < 2; refresh++) {
    const view = workspaceState(structuredClone(jobs), draft);
    assert.equal(view.current, null);
    assert.deepEqual(view.history, original);
  }
  assert.deepEqual(jobs, original);
  assert.equal(draft.mode, "quick");
});

test("a returned preview becomes current even when it contains no pending notes", () => {
  const old = {
    id: "old",
    state: "completed",
    items: [{ path: "Resource/archived.md" }],
  };
  const fresh = { id: "new", state: "preview", items: [] };
  const draft = { preparingFor: "old" };
  assert.deepEqual(workspaceState([fresh, old], draft), {
    current: fresh,
    history: [old],
  });
  assert.equal(draft.preparingFor, undefined);
});

test("failed submission keeps preparation; changing scope cannot inherit the previous task view", () => {
  const old = { id: "old", state: "completed" };
  const draft = { preparingFor: old.id };
  assert.equal(workspaceState([old], draft).current, null);
  const active = {
    id: "other-vault",
    state: "partial",
    blocking_task: { id: "owner" },
  };
  assert.equal(workspaceState([active], draft).current, active);
  assert.equal(workspaceState([], draft).current, undefined);
});

test("preparation never hides unfinished work or its recovery controls", () => {
  for (const state of [
    "running",
    "partial",
    "failed",
    "paused",
    "waiting_task",
    "waiting_file",
    "rollback_conflict",
  ]) {
    const job = { id: "unfinished", state };
    assert.equal(workspaceState([job], { preparingFor: job.id }).current, job);
  }
});

test("a successful identical submission reveals its reused preview", async () => {
  const { previewSubmitted } =
    await import("../../src/fiber_bundle/web/organization_workspace.js");
  const preview = { id: "existing-preview", state: "preview", items: [] };
  const draft = { preparingFor: preview.id };
  assert.equal(workspaceState([preview], draft).current, null);
  previewSubmitted(draft);
  assert.equal(workspaceState([preview], draft).current, preview);
  assert.equal(draft.previewVersion, 1);
});
