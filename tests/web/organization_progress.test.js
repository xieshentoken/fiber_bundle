import test from "node:test";
import assert from "node:assert/strict";
import {
  mergeProgress,
  itemCategory,
  maintenanceSummary,
  orderedMaintenance,
  maintenancePhase,
  evidenceFailure,
  manuallyResolved,
  previewInvalidated,
} from "../../src/fiber_bundle/web/organization_progress.js";
test("evidence diagnostics distinguish MOC definitions, body quotes and formatting", () => {
  assert.match(evidenceFailure("scope_quote_missing"), /MOC.*主题定义/);
  assert.match(evidenceFailure("body_quote_missing"), /正文引文/);
  assert.match(evidenceFailure("body_quote_overlap"), /重叠/);
  assert.match(evidenceFailure("format"), /格式/);
  assert.equal(evidenceFailure("untrusted input"), null);
  assert.equal(evidenceFailure("constructor"), null);
  assert.equal(evidenceFailure("__proto__"), null);
});
test("planning counts evaluated notes; unevaluated candidates are not manual reviews", () => {
  const job = {
    stage: "planning",
    items: [
      { evaluated: false, status: "review" },
      { evaluated: true, status: "review" },
      { evaluated: true, add: ["m"], remove: [], state: "pending" },
    ],
  };
  assert.equal(itemCategory(job.items[0], job), "waiting");
  assert.deepEqual(maintenanceSummary(job), {
    changed: 1,
    review: 1,
    waiting: 1,
    error: 0,
    unchanged: 0,
    processed: 0,
    total: 3,
    evaluated: 2,
    done: 0,
    additions: 1,
    removals: 0,
    manual: 0,
  });
});
test("manual resolution is processed while preserving the original diagnosis and plan", () => {
  const item = {
      entity: "note:one",
      evaluated: true,
      state: "pending",
      status: "review",
      selected: [],
      add: ["old-proposal"],
      remove: ["existing-manual"],
      manual: ["existing-manual"],
      target: "03-Resources/old-proposal.md",
      analysis_error: "organization_model_evidence_invalid",
      analysis_detail: "body_quote_missing",
      error: "maintenance_note_changed",
      manual_resolution: {
        state: "resolved",
        method: "manual_moc",
        moc_ids: ["thermal", "materials"],
        resolved_at: 1792300000,
        source_sha256: "source",
      },
    },
    original = JSON.parse(JSON.stringify(item)),
    job = { stage: "planning", items: [item] };
  assert.equal(manuallyResolved(item), true);
  assert.equal(itemCategory(item, job), "processed");
  assert.deepEqual(maintenanceSummary(job), {
    changed: 0,
    review: 0,
    waiting: 0,
    error: 0,
    unchanged: 0,
    processed: 1,
    total: 1,
    evaluated: 1,
    done: 1,
    additions: 0,
    removals: 0,
    manual: 0,
  });
  assert.deepEqual(item, original);
  assert.equal(
    manuallyResolved({
      ...item,
      manual_resolution: { state: "pending", method: "manual_moc" },
    }),
    false,
  );
  assert.equal(
    itemCategory({ ...item, manual_resolution: null }, job),
    "error",
  );
});
test("deep differences retain manual removal impact and application progress", () => {
  const job = {
    stage: "applying",
    items: [
      {
        evaluated: true,
        add: ["b"],
        remove: ["a"],
        manual: ["a"],
        state: "done",
      },
      { evaluated: true, state: "pending", analysis_error: "unknown" },
    ],
  };
  const s = maintenanceSummary(job);
  assert.equal(s.manual, 1);
  assert.equal(s.removals, 1);
  assert.equal(s.done, 1);
  assert.equal(s.error, 1);
  assert.equal(
    maintenancePhase({ ...job, index_state: "pending" }),
    "更新索引",
  );
});
test("unfinished tasks precede newer completed history without changing stored order", () => {
  const jobs = [
    { id: "old", maintenance: true, state: "blocked", created_at: 1 },
    { id: "new", maintenance: true, state: "completed", created_at: 2 },
  ];
  assert.equal(orderedMaintenance(jobs)[0].id, "old");
  assert.equal(jobs[0].id, "old");
});
test("dependency-invalidated previews stay in history while old unchanged previews remain actionable", () => {
  const now = 200_000;
  const jobs = [
    {
      id: "expired",
      maintenance: true,
      state: "preview",
      created_at: 10,
      expires_at: 200,
      preview_validity: { state: "invalidated" },
    },
    { id: "done", maintenance: true, state: "completed", created_at: 20 },
    {
      id: "fresh",
      maintenance: true,
      state: "preview",
      created_at: 5,
      expires_at: 201,
    },
    { id: "paused", maintenance: true, state: "paused", created_at: 1 },
  ];
  const before = JSON.parse(JSON.stringify(jobs));
  assert.deepEqual(
    orderedMaintenance(jobs, now).map((j) => j.id),
    ["fresh", "paused", "done", "expired"],
  );
  assert.equal(previewInvalidated(jobs[2], now + 30 * 86400_000), false);
  assert.equal(previewInvalidated(jobs[0], now), true);
  assert.equal(
    previewInvalidated({ state: "completed", expires_at: 0 }, now),
    false,
  );
  assert.deepEqual(jobs, before);
});

test("ledger progress preserves an invalidated preview only for the identical plan", () => {
  const old = {
    id: "old",
    state: "preview",
    plan_sha256: "a",
    preview_validity: { state: "invalidated" },
  };
  const live = { id: "live", state: "running", completed: 2 };
  const result = mergeProgress(
    { jobs: [old, live] },
    {
      jobs: [
        { ...old, preview_validity: undefined },
        { ...live, completed: 3, pause_requested: true },
      ],
    },
  );
  assert(previewInvalidated(result.jobs[0]));
  assert.equal(result.jobs[1].completed, 3);
  assert.match(maintenancePhase(result.jobs[1]), /正在暂停/);
  assert.equal(
    mergeProgress(
      { jobs: [old] },
      { jobs: [{ id: "old", state: "preview", plan_sha256: "b" }] },
    ).jobs[0].preview_validity,
    undefined,
  );
});
