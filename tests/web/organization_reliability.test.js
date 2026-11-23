import test from "node:test";
import assert from "node:assert/strict";
import { installDOM } from "./reliability_dom_fixture.js";
globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { appendExecutionProgress } =
  await import("../../src/fiber_bundle/web/organization_progress_view.js");
const { appendExecutionStatus } =
  await import("../../src/fiber_bundle/web/organization_execution_status.js");
const { appendArchivePreview } =
  await import("../../src/fiber_bundle/web/organization_archive_readiness.js");
const { appendStageSummary } =
  await import("../../src/fiber_bundle/web/organization_stages.js");
const { maintenancePhase, mergeProgress } =
  await import("../../src/fiber_bundle/web/organization_progress.js");
const { createOrganizationView } =
  await import("../../src/fiber_bundle/web/organization.js");
const { enhanceSelects } =
  await import("../../src/fiber_bundle/web/select_controls.js");
const { indexProblem } =
  await import("../../src/fiber_bundle/web/note_index_errors.js");
const { openVaultIndexDetails } =
  await import("../../src/fiber_bundle/web/vault_index_details.js");
const { setLocale } = await import("../../src/fiber_bundle/web/i18n/index.js");
const { maintenanceSection } =
  await import("../../src/fiber_bundle/web/organization_maintenance.js");
const { createAdminApi } = await import("../../src/fiber_bundle/web/api.js");
const { createSession } = await import("../../src/fiber_bundle/web/session.js");

test("progress DOM uses the persisted phase counts for preview, apply, finalization and closed results in both languages", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      for (const [phase, completed] of [
        ["analysis", 7],
        ["preview", 15],
        ["applying", 8],
        ["finalizing", 8],
        ["closed", 14],
      ]) {
        const host = dom.document.createElement("section");
        const job = {
          state:
            phase === "closed"
              ? "closed"
              : phase === "preview"
                ? "preview"
                : "partial",
          confirmed: phase !== "preview",
          mode: "quick",
          items: [],
          execution_progress: {
            schema: "organization-progress/v1",
            phase,
            completed,
            total: 15,
            counts: {
              analyzed: 15,
              processed: completed,
              mounted: 10,
              archived: 9,
              waiting: 1,
              review: 5,
              archive_pending: phase === "finalizing" ? 1 : 0,
            },
          },
        };
        appendExecutionProgress(host, job);
        appendStageSummary(host, job);
        const progress = host.querySelector("progress");
        assert.equal(progress.max, 15);
        assert.equal(progress.value, completed);
        assert.equal(progress.getAttribute("value"), String(completed));
        assert.equal(progress.getAttribute("max"), "15");
        assert.match(
          progress.getAttribute("aria-valuetext"),
          new RegExp(`${completed} / 15`),
        );
        assert.match(
          host.children[0].textContent,
          new RegExp(`${completed} / 15`),
        );
        if (phase === "preview")
          assert.match(
            host.textContent,
            language === "en" ? /No changes or archives/ : /尚未应用或归档/,
          );
        if (phase === "closed")
          assert.match(
            host.textContent,
            language === "en" ? /Archived 9.*Waiting 1/ : /已归档 9.*等待 1/,
          );
        if (phase === "finalizing")
          assert.match(
            host.textContent,
            language === "en" ? /need finalization/ : /尚未收尾/,
          );
      }
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("closed historical deferral never advises ending or resuming the closed task", () => {
  const dom = installDOM();
  try {
    const host = dom.document.createElement("section"),
      job = {
        state: "closed",
        confirmed: true,
        error: "maintenance_membership_baseline_changed",
        items: [{ archive_wait: { state: "blocked" } }],
      };
    appendArchivePreview(host, job);
    appendExecutionStatus(host, job, () => "请结束任务");
    assert.doesNotMatch(host.textContent, /先.*结束|请结束|恢复未完成/);
  } finally {
    dom.restore();
  }
});

test("known diagnostics retain exact directory, operation and conflict facts rather than generic unavailable text", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const host = dom.document.createElement("section");
      const job = {
        state: "partial",
        error: "note directory changed",
        error_detail: {
          reason: "directory_changed",
          phase: "archive_finalize_inventory",
          path: "00-Inbox",
          changed_fields: ["mtime_ns"],
          retryable_read: true,
        },
        items: [{ archive_operation: "original-op", phase: "members_done" }],
      };
      appendExecutionStatus(host, job, () => "generic fallback");
      assert.match(
        host.textContent,
        /00-Inbox.*archive_finalize_inventory.*mtime_ns.*original-op/,
      );
      assert.match(
        host.textContent,
        language === "en" ? /original operation was written/ : /原操作已写入/,
      );
      assert.equal(maintenancePhase(job), "已写入 · 归档收尾待核对");
      assert.match(
        indexProblem("vault_sync_revision_conflict"),
        language === "en" ? /observation revision conflicts/ : /观察修订.*冲突/,
      );
      assert.doesNotMatch(host.textContent, /generic fallback/);
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("an older task response cannot regress counters while a newer index observation remains visible", () => {
  const prior = {
    id: "task",
    state: "closed",
    updated_at: 20,
    completed: 14,
    items: [],
  };
  const result = mergeProgress(
    { jobs: [prior] },
    {
      jobs: [
        {
          ...prior,
          state: "partial",
          updated_at: 10,
          completed: 8,
          index_observation: { state: "ready", observed_at: 30 },
        },
      ],
    },
  );
  assert.equal(result.jobs[0].completed, 14);
  assert.equal(result.jobs[0].state, "closed");
  assert.equal(result.jobs[0].index_observation.state, "ready");
});

test("hidden select projections settle the root observer and preserve subsequent scope changes", () => {
  const dom = installDOM();
  const select = dom.document.createElement("select");
  select.hidden = true;
  dom.document.body.append(select);
  const controls = enhanceSelects(dom.document.body);
  try {
    assert.equal(dom.flushMutations(), 0);
    select.hidden = true;
    assert(dom.flushMutations() <= 2);
    select.hidden = false;
    assert.equal(dom.flushMutations(), 3);
    assert.equal(select.parentElement.hidden, false);
    select.hidden = true;
    assert.equal(dom.flushMutations(), 3);
    assert.equal(select.parentElement.hidden, true);
  } finally {
    controls.destroy();
    dom.restore();
  }
});

test("catalog failure preserves shared graph controls through the rendered maintenance fallback", async () => {
  const dom = installDOM();
  let fail = false;
  const value = {
    jobs: [],
    items: [],
    mocs: [],
    total: 0,
    next_offset: null,
    policy: {
      revision: 1,
      settings: {
        mode: "off",
        include: [],
        exclude: [],
        conditions: [],
        rules: [],
      },
    },
  };
  const view = createOrganizationView(
    {
      list: async () => {
        if (fail) throw Error("synthetic directory conflict");
        return value;
      },
      progress: async () => ({ jobs: [] }),
    },
    () => ({ client_id: "c", vault_id: "v" }),
  );
  const policy = dom.document.createElement("p");
  policy.id = "graph-policy";
  view.attachAnalysis([policy]);
  dom.document.body.append(view.host);
  try {
    await view.refresh();
    assert.equal(dom.document.getElementById("graph-policy"), policy);
    fail = true;
    await view.refresh();
    assert.equal(dom.document.getElementById("graph-policy"), policy);
    assert.match(
      view.host.textContent,
      /目录读取未完成：synthetic directory conflict/,
    );
    assert.doesNotMatch(view.host.textContent, /请先点击“更新此知识库索引”/);
    assert.equal(
      view.host.querySelector("#organization-maintenance")?.id,
      "organization-maintenance",
    );
  } finally {
    view.clear();
    dom.restore();
  }
});

test("classic index destination reads the selected vault and never updates merely by opening", async () => {
  const dom = installDOM(),
    calls = [];
  const buttons = Array.from({ length: 2 }, (_, index) => {
    const button = dom.document.createElement("button");
    button.dataset.vaultId = "chosen";
    button.className = "primary ui-small";
    button.textContent = `Other vault action ${index}`;
    return button;
  });
  const list = dom.document.createElement("div"),
    row = dom.document.createElement("article");
  list.id = "vault-list";
  row.className = "row vault-row ui-card";
  row.dataset.vaultId = "chosen";
  dom.document.body.append(...buttons, list);
  list.append(row);
  try {
    await openVaultIndexDetails(
      {
        clients: async () => [
          {
            id: "reader",
            name: "Reader",
            active: true,
            grants: [{ vault_id: "chosen", can_query: true }],
          },
        ],
        noteIndexStatus: async (client, vault) => {
          calls.push([client, vault]);
          return {
            state: "ready",
            notes: 736,
            sync: { pending: 0, needs_attention: 0 },
          };
        },
        updateNoteIndex: () => assert.fail("opening details must not update"),
      },
      "chosen",
    );
    assert.deepEqual(calls, [["reader", "chosen"]]);
    assert.match(row.textContent, /736.*待更新 0.*需核对 0/);
    assert.equal(row.querySelector(".vault-index-details").parentElement, row);
    for (const [index, button] of buttons.entries()) {
      assert.equal(button.textContent, `Other vault action ${index}`);
      assert.equal(button.querySelector(".vault-index-details"), null);
    }
  } finally {
    dom.restore();
  }
});

test("HTTP resume diagnostics reach the GUI error with exact file, phase and sha256 evidence without retry", async () => {
  for (const language of ["zh-CN", "en"]) {
    setLocale(language);
    let calls = 0;
    const diagnostic = {
      reason: "committed_file_changed",
      phase: "archive_committed_verify",
      path: "Map/热管理 MOC.md",
      changed_fields: ["sha256"],
      retryable_read: false,
    };
    const api = createAdminApi(
      createSession(),
      { onConnection() {}, onUnauthorized() {} },
      async () => {
        calls++;
        return {
          ok: false,
          status: 409,
          json: async () => ({
            error: "placement_note_changed",
            error_detail: diagnostic,
          }),
        };
      },
    );
    await assert.rejects(
      api.organization.maintenanceAction("original", "resume", {
        client_id: "c",
        vault_id: "v",
      }),
      (error) => {
        assert.deepEqual(error.diagnostic, diagnostic);
        assert.equal(error.status, 409);
        assert.match(
          error.message,
          /Map\/热管理 MOC\.md.*archive_committed_verify.*sha256/,
        );
        assert.match(
          error.message,
          language === "en"
            ? /committed target file or exchange backup changed/
            : /目标文件或交换备份发生变化/,
        );
        return true;
      },
    );
    assert.equal(calls, 1);
  }
  setLocale("zh-CN");
});

test("expanded closed item errors describe history without offering recovery or closure", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const job = {
        id: "closed",
        maintenance: true,
        state: "closed",
        stage: "applying",
        mode: "quick",
        confirmed: true,
        completed: 0,
        new_mocs: [],
        items: [
          {
            entity: "note",
            title: "Original",
            path: "Inbox/Original.md",
            evaluated: true,
            state: "failed",
            status: "review",
            error: "maintenance_membership_baseline_changed",
            before: [],
            after: [],
            add: [],
            remove: [],
          },
        ],
      };
      const host = maintenanceSection(
        { jobs: [job], mocs: [] },
        {},
        {},
        () => {},
        { mode: "quick", title: "", topic: "", preparingFor: job.id },
      );
      const history = host.querySelector('[data-view-key="job:closed"]');
      history.open = true;
      history.listeners.toggle();
      const row = host.querySelector(".task-row");
      assert.match(
        row.textContent,
        language === "en"
          ? /Historical error: maintenance_membership_baseline_changed/
          : /历史错误：maintenance_membership_baseline_changed/,
      );
      assert.doesNotMatch(
        row.textContent,
        /请结束任务|先.*结束|恢复未完成|End the task|Resume unfinished/,
      );
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("scope and symlink diagnostics identify the actual parent path rather than waiting for sync", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const host = dom.document.createElement("section");
      appendExecutionStatus(
        host,
        {
          state: "partial",
          error: "cloud_symlink_not_allowed",
          error_detail: {
            reason: "scope_changed",
            phase: "note_inventory",
            path: "Resource/parent",
            changed_fields: ["ancestor"],
          },
        },
        () => "generic waiting for sync",
      );
      assert.match(
        host.textContent,
        language === "en"
          ? /parent directory, access scope or symbolic link changed/
          : /父目录、访问范围或符号链接发生变化/,
      );
      assert.match(
        host.textContent,
        /Resource\/parent.*note_inventory.*ancestor/,
      );
      assert.doesNotMatch(host.textContent, /generic waiting for sync/);
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});
