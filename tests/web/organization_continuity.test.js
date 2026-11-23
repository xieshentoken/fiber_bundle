import test from "node:test";
import assert from "node:assert/strict";
import {
  maintenancePhase,
  orderedMaintenance,
} from "../../src/fiber_bundle/web/organization_progress.js";
import { taskActions } from "../../src/fiber_bundle/web/organization_task_controls.js";
import { executionStatusModel } from "../../src/fiber_bundle/web/organization_execution_status.js";

test("a legacy blocked job explains its owner instead of reporting another execution failure", () => {
  const job = {
    state: "partial",
    stage: "applying",
    confirmed: true,
    items: [],
    blocking_task: { id: "old", completed: 21, total: 33 },
  };
  assert.equal(maintenancePhase(job), "等待原任务处理");
  assert.equal(executionStatusModel(job).interrupted, false);
  assert(!taskActions(job).some((a) => a.operation === "resume"));
  assert(
    taskActions({ ...job, blocking_task: null, state: "waiting_task" }).some(
      (a) => a.operation === "resume",
    ),
  );
});

test("closed tasks preserve results and are not presented as resumable or undoable", () => {
  const job = {
    id: "closed",
    state: "closed",
    maintenance: 1,
    stage: "applying",
    items: [],
  };
  assert.equal(maintenancePhase(job), "已结束 · 已完成结果保留");
  assert.deepEqual(taskActions(job), []);
  assert.equal(
    orderedMaintenance([job, { ...job, id: "active", state: "partial" }])[0].id,
    "active",
  );
});
