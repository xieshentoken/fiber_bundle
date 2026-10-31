import test from "node:test";
import assert from "node:assert/strict";
import {
  maintenancePhase,
  effectiveIndexState,
} from "../../src/fiber_bundle/web/organization_progress.js";
import { executionStatusModel } from "../../src/fiber_bundle/web/organization_execution_status.js";
import { taskActions } from "../../src/fiber_bundle/web/organization_task_controls.js";

test("interruption remains primary after the vault index recovers", () => {
  const job = {
    state: "partial",
    stage: "applying",
    index_state: "failed",
    activity: { updated_at: 100 },
    index_observation: { state: "ready", after_task: true },
    items: [
      { state: "done" },
      { state: "failed", path: "Inbox/source.md" },
      { state: "pending" },
    ],
  };
  assert.equal(maintenancePhase(job), "整理已中断");
  assert.equal(effectiveIndexState(job), "ready");
  assert.deepEqual(executionStatusModel(job), {
    interrupted: true,
    failed: 1,
    remaining: 1,
    failedPath: "Inbox/source.md",
    reason: null,
    indexState: "ready",
    lastProgress: 100,
  });
  assert.equal(
    taskActions(job).find((a) => a.label === "恢复未完成整理")?.operation,
    "resume",
  );
});
test("old index or missing observation does not clear a task failure", () => {
  assert.equal(
    effectiveIndexState({
      index_state: "failed",
      index_observation: { state: "ready", after_task: false },
    }),
    "failed",
  );
  assert.equal(effectiveIndexState({ index_state: "pending" }), "pending");
  assert.equal(
    maintenancePhase({
      state: "running",
      stage: "applying",
      index_state: "failed",
    }),
    "应用分类与归档",
  );
  assert.equal(
    maintenancePhase({
      state: "completed",
      index_state: "failed",
      index_observation: { state: "ready", after_task: true },
    }),
    "整理完成",
  );
  assert.equal(
    taskActions({
      state: "completed",
      index_state: "failed",
      index_observation: { state: "ready", after_task: true },
      items: [],
    }).length,
    0,
  );
});
test("cloud diagnosis names the affected file without treating raw values as UI copy", () => {
  const value = executionStatusModel({
    state: "partial",
    error_detail: { reason: "metadata_changed", path: "Map/Topic.md" },
  });
  assert.match(value.reason, /文件状态/);
  assert.equal(value.failedPath, "Map/Topic.md");
  assert.equal(
    executionStatusModel({ error_detail: { reason: "constructor" } }).reason,
    null,
  );
});
