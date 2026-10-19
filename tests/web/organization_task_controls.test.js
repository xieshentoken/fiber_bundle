import test from "node:test";
import assert from "node:assert/strict";
import {
  taskActions,
  unknownOutcome,
} from "../../src/fiber_bundle/web/organization_task_controls.js";
import {
  maintenancePhase,
  jobNames,
} from "../../src/fiber_bundle/web/organization_progress.js";

test("cancelled tasks never resume; pause and cancellation remain distinct", () => {
  const job = { mode: "quick", state: "running", items: [], confirmed: true };
  assert.deepEqual(
    taskActions(job).map((v) => v.operation),
    ["pause", "cancel"],
  );
  assert.match(taskActions(job)[1].label, /撤销/);
  assert.deepEqual(taskActions({ ...job, state: "cancelled" }), []);
  assert.deepEqual(
    taskActions({ ...job, state: "paused" }).map((v) => v.operation),
    ["resume", "cancel"],
  );
  assert.match(
    maintenancePhase({ state: "cancelled", rollback_completed: true }),
    /变更已撤销/,
  );
  assert.equal(jobNames.rollback_conflict, "撤销待核对");
});
test("model supplement requires explicit action and unknown retry requires consent", () => {
  const job = {
    mode: "quick",
    state: "preview",
    confirmed: false,
    items: [{ selected: [], evaluated: true }],
  };
  assert(taskActions(job).some((v) => v.operation === "supplement"));
  assert(
    !taskActions({ ...job, items: [{ selected: ["m"] }] }).some(
      (v) => v.operation === "supplement",
    ),
  );
  const unknown = {
    ...job,
    items: [{ analysis_error: "organization_model_result_unknown" }],
  };
  assert(
    taskActions(unknown).find((v) => v.operation === "retry_unknown").consent,
  );
  assert(!taskActions(unknown).some((v) => v.operation === "supplement"));
  assert.match(
    unknownOutcome({ reason: "model_timeout", created_at: 1792229313 }),
    /超时.*其余项目继续/,
  );
});

test("cache recovery gates missing-window supplement and unknown retry separately", () => {
  const job = {
    state: "preview",
    mode: "quick",
    confirmed: false,
    cache_recovery: { parent: "old" },
    items: [
      { selected: [], analysis_error: "organization_model_cache_incomplete" },
      { analysis_error: "organization_model_result_unknown" },
    ],
  };
  const actions = taskActions(job);
  assert(actions.find((a) => a.operation === "supplement_missing").consent);
  assert(!actions.some((a) => a.operation === "supplement"));
  assert(actions.find((a) => a.operation === "retry_unknown").consent);
  assert(
    !taskActions({ ...job, confirmed: true }).some(
      (a) => a.operation === "supplement_missing",
    ),
  );
});

test("resolved manual items never trigger model supplement or retry from historical errors", () => {
  const resolution = {
      state: "resolved",
      method: "manual_moc",
      moc_ids: ["one", "two"],
    },
    job = {
      state: "preview",
      mode: "quick",
      confirmed: false,
      items: [
        { selected: [], manual_resolution: resolution },
        {
          analysis_error: "organization_model_result_unknown",
          manual_resolution: resolution,
        },
        {
          analysis_error: "organization_model_cache_incomplete",
          manual_resolution: resolution,
        },
      ],
    };
  const original = JSON.parse(JSON.stringify(job));
  for (const cache_recovery of [undefined, { parent: "old" }]) {
    const actions = taskActions({ ...job, cache_recovery });
    assert(
      !actions.some((a) =>
        ["supplement", "supplement_missing", "retry_unknown"].includes(
          a.operation,
        ),
      ),
    );
    assert(actions.some((a) => a.operation === "pause"));
    assert(actions.some((a) => a.operation === "cancel"));
  }
  assert.deepEqual(job, original);
  const pending = {
    ...job,
    items: [
      ...job.items,
      { analysis_error: "organization_model_result_unknown" },
    ],
  };
  assert(taskActions(pending).some((a) => a.operation === "retry_unknown"));
});
