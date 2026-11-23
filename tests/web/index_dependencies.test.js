import test from "node:test";
import assert from "node:assert/strict";
globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { validIndexActivity } =
  await import("../../src/fiber_bundle/web/index_activity_api.js");
const { indexActivitySummary } =
  await import("../../src/fiber_bundle/web/index_activity.js");
const { indexDependencyText, noteIndexOutcome, indexIsRunning } =
  await import("../../src/fiber_bundle/web/note_index_errors.js");
const { taskActions, archiveVerificationText } =
  await import("../../src/fiber_bundle/web/organization_task_controls.js");
const { setLocale, translate } =
  await import("../../src/fiber_bundle/web/i18n/index.js");

const dependency = {
  reason: "placement_reconciliation_required",
  recovery: "verify_committed_archives",
  operations: [
    {
      id: "synthetic-operation",
      state: "reconcile",
      kind: "note_placement",
      job_ids: ["synthetic-owner"],
    },
  ],
};
const activity = {
  schema: "note-index-activity/v1",
  observed_at: 101,
  vaults: [
    {
      vault_id: "vault",
      name: "Vault",
      state: "blocked",
      pending_paths: 0,
      needs_attention: 0,
      updated_at: 100,
      dependency,
    },
  ],
};

test("blocked index payload preserves operation and task and does not imply a running index", () => {
  assert(validIndexActivity(activity));
  const invalid = structuredClone(activity);
  delete invalid.vaults[0].dependency;
  assert.equal(validIndexActivity(invalid), false);
  assert.equal(
    indexIsRunning({ state: "blocked", sync: { dependency } }),
    false,
  );
  for (const [locale, pattern] of [
    ["zh-CN", /等待归档核验/],
    ["en", /Waiting for archive verification/],
  ]) {
    setLocale(locale);
    assert.match(indexActivitySummary({ value: activity }).label, pattern);
    assert.match(noteIndexOutcome({ state: "blocked", dependency }), pattern);
    const detail = indexDependencyText(dependency);
    assert.match(detail, /synthetic-operation/);
    assert.match(detail, /synthetic-owner/);
    assert.match(translate("等待归档核验"), pattern);
  }
  setLocale("zh-CN");
});

test("the archive-only action and receipt distinguish success, conflict and an unknown outcome", () => {
  const job = {
    state: "partial",
    confirmed: true,
    items: [],
    archive_verification: { available: true },
  };
  setLocale("zh-CN");
  assert.equal(
    taskActions(job).find((a) => a.operation === "verify_committed_archives")
      .label,
    "仅核验已提交归档",
  );
  assert(
    !taskActions({ ...job, archive_verification: { available: false } }).some(
      (a) => a.operation === "verify_committed_archives",
    ),
  );
  assert(
    !taskActions({ ...job, pause_requested: true }).some(
      (a) => a.operation === "verify_committed_archives",
    ),
  );
  const receipt = { state: "verified", operations: ["synthetic-operation"] };
  assert.match(archiveVerificationText(receipt), /核验完成；任务保持中断/);
  setLocale("en");
  assert.equal(
    taskActions(job).find((a) => a.operation === "verify_committed_archives")
      .label,
    "Verify committed archives only",
  );
  assert.match(
    archiveVerificationText({ ...receipt, state: "unknown" }),
    /unconfirmed; no automatic retry/,
  );
  assert.match(
    archiveVerificationText({
      ...receipt,
      state: "conflict",
      error: "placement_link_sources_changed",
      detail: { reason: "link_paths_changed", changed_fields: ["path_sha256"] },
    }),
    /link_paths_changed.*path_sha256/,
  );
  setLocale("zh-CN");
});

test("unowned layout/rebind blockers point to original operations rather than archive-only recovery", () => {
  const generic = {
    ...dependency,
    recovery: "review_original_operation",
    owner_lookup_failed: true,
    operations: [
      {
        id: "synthetic-rebind",
        state: "reconcile",
        kind: "publication_rebind",
        job_ids: [],
      },
    ],
  };
  const value = structuredClone(activity);
  value.vaults[0].dependency = generic;
  assert(validIndexActivity(value));
  setLocale("zh-CN");
  assert.match(indexActivitySummary({ value }).label, /等待操作核验/);
  const message = indexDependencyText(generic);
  assert.match(message, /所属任务暂无法读取/);
  assert.match(message, /synthetic-rebind/);
  assert.doesNotMatch(message, /选择“仅核验已提交归档”/);
  setLocale("en");
  assert.match(indexDependencyText(generic), /original operation/);
  setLocale("zh-CN");
});

test("catalog and graph dependency errors name the original operation instead of prompting an index retry", async () => {
  const { createAdminApi } = await import("../../src/fiber_bundle/web/api.js");
  const { createSession } =
    await import("../../src/fiber_bundle/web/session.js");
  const { graphLoadFailure } =
    await import("../../src/fiber_bundle/web/knowledge_graph_setup.js");
  const api = createAdminApi(
    createSession(),
    { onConnection() {}, onUnauthorized() {} },
    async () => ({
      ok: false,
      status: 409,
      json: async () => ({
        error: "placement_reconciliation_required",
        dependency,
      }),
    }),
  );
  await assert.rejects(
    api.organization.list({ client_id: "client", vault_id: "vault" }),
    (error) => {
      assert.match(error.message, /等待归档核验/);
      assert.match(error.message, /synthetic-operation/);
      assert.match(graphLoadFailure(error), /仅核验已提交归档/);
      assert.doesNotMatch(graphLoadFailure(error), /请更新.*索引/);
      return true;
    },
  );
});

test("a verified archive keeps its old error as history and leaves pending items unchanged", async () => {
  const { itemCategory, maintenanceSummary } =
    await import("../../src/fiber_bundle/web/organization_progress.js");
  const item = {
    state: "done",
    phase: "archived",
    archive_operation: "operation",
    error: "historical-conflict",
  };
  const pending = {
    state: "pending",
    evaluated: true,
    target: "Resource/pending.md",
  };
  const job = {
    state: "partial",
    items: [item, pending],
    archive_verifications: [{ state: "verified", operations: ["operation"] }],
  };
  assert.equal(itemCategory(item, job), "processed");
  assert.equal(maintenanceSummary(job).error, 0);
  assert.equal(maintenanceSummary(job).done, 1);
  assert.equal(item.error, "historical-conflict");
  assert.equal(pending.state, "pending");
});
