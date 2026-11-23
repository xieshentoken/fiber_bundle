import test from "node:test";
import assert from "node:assert/strict";
import { installDOM } from "./reliability_dom_fixture.js";

globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { setLocale, translate } =
  await import("../../src/fiber_bundle/web/i18n/index.js");
const { archiveFinalizationVerified, maintenancePhase } =
  await import("../../src/fiber_bundle/web/organization_progress.js");
const { organizationDiagnostic } =
  await import("../../src/fiber_bundle/web/organization_diagnostics.js");
const { appendExecutionStatus, executionStatusModel } =
  await import("../../src/fiber_bundle/web/organization_execution_status.js");
const { appendExecutionProgress, executionProgress } =
  await import("../../src/fiber_bundle/web/organization_progress_view.js");
const { appendStageSummary } =
  await import("../../src/fiber_bundle/web/organization_stages.js");

function archivedTask() {
  return {
    confirmed: true,
    state: "partial",
    stage: "applying",
    index_state: "ready",
    completed: 1,
    error: "note directory changed",
    error_detail: {
      changed_fields: ["mtime_ns", "ctime_ns"],
      observed_at: 1794013147.868725,
      path: ".",
      phase: "archive_finalize_inventory",
      reason: "directory_changed",
      retryable_read: true,
    },
    items: [
      {
        state: "done",
        phase: "archived",
        archive_operation: "original-op",
        error: "note directory changed",
      },
      { state: "pending", phase: "analysis", error: null },
    ],
    archive_verifications: [
      {
        state: "verified",
        operations: ["original-op"],
        verified_at: 1794038567.0085561,
      },
    ],
    execution_progress: {
      schema: "organization-progress/v1",
      phase: "finalizing",
      completed: 1,
      total: 2,
      counts: {
        analyzed: 2,
        processed: 1,
        mounted: 1,
        archived: 1,
        waiting: 0,
        review: 0,
        archive_pending: 0,
      },
    },
  };
}

test("verified real-shape archive errors render as history while the remaining item stays stopped in both hosts' shared view", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const job = archivedTask(),
        before = structuredClone(job);
      const host = dom.document.createElement("section");
      const heading = dom.document.createElement("h3");
      heading.textContent = translate(maintenancePhase(job));
      host.append(heading);
      appendExecutionProgress(host, job);
      appendExecutionStatus(host, job, () =>
        assert.fail("history must not request recovery"),
      );
      appendStageSummary(host, job);
      assert.match(
        heading.textContent,
        language === "en"
          ? /Archive verified.*remains stopped/
          : /归档已核验.*整理仍中断/,
      );
      assert.match(
        host.textContent,
        language === "en" ? /diagnostic below is historical/ : /以下为历史诊断/,
      );
      assert.match(
        host.textContent,
        language === "en" ? /0 failed.*1 pending/ : /0 项失败，1 项待处理/,
      );
      assert.match(host.textContent, /1 \/ 2/);
      assert.match(host.textContent, /note directory changed/);
      assert.match(
        host.textContent,
        /\. · archive_finalize_inventory · mtime_ns · ctime_ns · original-op/,
      );
      assert.doesNotMatch(
        host.textContent,
        /归档收尾核对尚未完成|核对失败的文件|finalization is unverified/,
      );
      assert.equal(executionStatusModel(job).failedPath, undefined);
      assert.equal(executionProgress(job).phase, "applying");
      assert.equal(organizationDiagnostic(job).historical, true);
      assert.deepEqual(
        job,
        before,
        "rendering retains original error, detail and pending fields",
      );
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("unverified history and every unresolved or unknown owned operation retain the original finalization warning", () => {
  const changes = [
    (job) => {
      job.archive_verifications = [];
    },
    (job) => {
      job.archive_verifications[0].operations = ["other-op"];
    },
    (job) => {
      job.archive_verifications[0].verified_at = 1;
    },
    (job) => {
      delete job.archive_verifications[0].verified_at;
    },
    (job) => {
      job.archive_verifications.push({
        state: "unknown",
        operations: ["original-op"],
      });
    },
    (job) => {
      job.archive_verifications.push({
        state: "verifying",
        operations: ["original-op"],
      });
    },
    (job) => {
      job.archive_verifications.push({
        state: "conflict",
        operations: ["original-op"],
      });
    },
    (job) => {
      job.items.push({
        state: "done",
        phase: "archived",
        archive_operation: "unverified-op",
      });
    },
    (job) => {
      job.items[0].phase = "members_done";
    },
    (job) => {
      job.items[0].state = "failed";
    },
    (job) => {
      job.error_detail.observed_at =
        job.archive_verifications[0].verified_at + 1;
    },
    (job) => {
      job.error_detail.operation_id = "different-operation";
    },
    (job) => {
      job.error = "new_unresolved_error";
    },
    (job) => {
      job.index_observation = { state: "blocked" };
    },
    (job) => {
      job.index_dependency = { operations: [{ id: "still-blocked" }] };
    },
    (job) => {
      job.execution_progress.counts.archive_pending = 1;
    },
    (job) => {
      job.state = "running";
    },
  ];
  for (const change of changes) {
    const job = archivedTask();
    change(job);
    assert.equal(archiveFinalizationVerified(job), false);
    assert.equal(maintenancePhase(job), "已写入 · 归档收尾待核对");
    assert.match(
      String(organizationDiagnostic(job).text),
      /归档收尾核对尚未完成/,
    );
    assert.equal(organizationDiagnostic(job).historical, undefined);
    assert.equal(executionStatusModel(job).failedPath, ".");
    assert.equal(executionProgress(job).phase, "finalizing");
  }
});

test("a verified receipt does not rewrite a later unrelated diagnostic and accepts the public receipt projection", () => {
  const job = archivedTask();
  job.error = "maintenance_membership_baseline_changed";
  job.error_detail = {
    reason: "membership_changed",
    phase: "membership_apply",
    path: "Map/topic.md",
  };
  assert.equal(archiveFinalizationVerified(job), false);
  assert.match(String(organizationDiagnostic(job).text), /共享 MOC/);
  assert.equal(executionStatusModel(job).failedPath, "Map/topic.md");
  const projected = archivedTask();
  projected.archive_verification = {
    receipts: projected.archive_verifications,
  };
  delete projected.archive_verifications;
  assert.equal(archiveFinalizationVerified(projected), true);
});
