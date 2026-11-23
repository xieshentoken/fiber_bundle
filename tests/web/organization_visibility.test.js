import test from "node:test";
import assert from "node:assert/strict";
import { installDOM } from "./reliability_dom_fixture.js";
globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { maintenanceSection } =
  await import("../../src/fiber_bundle/web/organization_maintenance.js");
const { workspaceState, mountMaintenanceWorkspace } =
  await import("../../src/fiber_bundle/web/organization_workspace.js");
const { taskRecordSummary, appendRecordedPaths } =
  await import("../../src/fiber_bundle/web/organization_task_record.js");
const { appendCandidateCoverage } =
  await import("../../src/fiber_bundle/web/organization_candidate_coverage.js");
const { setLocale, msg } =
  await import("../../src/fiber_bundle/web/i18n/index.js");
const created = 1793923200;
const note = (title, extra = {}) => ({
  entity: title,
  title,
  path: `Inbox/${title}.md`,
  state: "done",
  status: "linked",
  phase: "archived",
  evaluated: true,
  before: [],
  after: [],
  add: [],
  remove: [],
  ...extra,
});
const task = (extra = {}) => ({
  id: "834a4cf1",
  maintenance: true,
  mode: "quick",
  stage: "applying",
  state: "closed",
  confirmed: true,
  completed: 2,
  created_at: created,
  closed_at: created + 120,
  new_mocs: [],
  items: [
    note("TSMC", { target: "Resources/TSMC.md" }),
    note("IMR", { target: "Resources/IMR.md" }),
    note("EELS", { state: "pending", phase: "pending", status: "review" }),
  ],
  ...extra,
});
function render(jobs, draft) {
  return maintenanceSection({ jobs, mocs: [] }, {}, {}, () => {}, draft);
}
function openRecord(host, id) {
  const record = host.querySelector(`[data-view-key="job:${id}"]`);
  record.open = true;
  record.listeners.toggle();
  return record;
}
function buttons(host) {
  return host.querySelectorAll("button").map((node) => node.textContent);
}

test("a newer ended task remains current while older paused work stays in history", () => {
  const old = task({
      id: "old-paused",
      state: "paused",
      created_at: created - 1,
    }),
    ended = task(),
    jobs = [old, ended],
    original = structuredClone(jobs);
  assert.deepEqual(workspaceState(jobs, {}), {
    current: ended,
    history: [old],
  });
  assert.deepEqual(jobs, original);
  for (const state of ["preview", "running", "paused", "partial"])
    assert.equal(
      workspaceState([old, { ...ended, state }], {}).current.state,
      state,
    );
});

test("latest closed results stay visible until preparation is opened, with all preserved history items", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const job = task(),
        original = structuredClone(job),
        host = render([job]);
      const current = host.querySelector(".maintenance-job");
      assert.equal(current.dataset.maintenanceJob, job.id);
      assert.equal(current.querySelectorAll(".task-row").length, 3);
      assert.match(current.textContent, /834a4cf1/);
      assert.match(
        current.textContent,
        language === "en" ? /Created:.*Ended:/ : /生成：.*结束：/,
      );
      const preparation = host
        .querySelectorAll("details")
        .find((node) =>
          node.dataset.viewKey?.startsWith("maintenance-preparation:"),
        );
      assert.equal(preparation.open, false);
      preparation.open = true;
      preparation.listeners.toggle();
      assert.equal(host.querySelector(".maintenance-job"), null);
      const record = openRecord(host, job.id);
      assert.match(record.firstChild.textContent, /834a4cf1/);
      assert.match(
        record.firstChild.textContent,
        language === "en" ? /Created:.*Ended:/ : /生成：.*结束：/,
      );
      assert.equal(record.querySelectorAll(".task-row").length, 3);
      assert.equal(
        record
          .querySelector('[data-filter="all"]')
          .getAttribute("aria-pressed"),
        "true",
      );
      assert.match(
        record.textContent,
        language === "en"
          ? /Historical processing record: 2 \/ 3.*task has ended/
          : /历史处理记录：2 \/ 3.*任务已结束/,
      );
      assert.match(
        record.textContent,
        language === "en"
          ? /Recorded task source: Inbox\/TSMC.md/
          : /任务记录来源：Inbox\/TSMC.md/,
      );
      assert.match(
        record.textContent,
        language === "en"
          ? /Recorded archive location: Resources\/TSMC.md/
          : /任务记录归档位置：Resources\/TSMC.md/,
      );
      assert.match(
        record.textContent,
        language === "en"
          ? /not a check of current file locations/
          : /不是当前文件位置检查/,
      );
      assert(
        !buttons(record).some((text) =>
          /确认并执行|恢复未完成|Confirm and apply|Resume unfinished/.test(
            text,
          ),
        ),
      );
      assert.deepEqual(job, original);
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("the latest preview shows only its included note, skip reasons, and planned targets", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const fresh = task({
        id: "fresh",
        state: "preview",
        confirmed: false,
        created_at: created + 300,
        closed_at: undefined,
        items: [
          note("EELS", {
            phase: "pending",
            state: "pending",
            target: "Resources/EELS.md",
          }),
        ],
        candidate_coverage: {
          complete: true,
          discovered_count: 3,
          included_count: 1,
          skipped_count: 2,
          skipped: [
            {
              path: "Inbox/unpublished.md",
              reason: "publication_not_committed",
            },
            { path: "Inbox/unknown.md", reason: "future_reason" },
          ],
        },
      });
      const host = render([task(), fresh]),
        card = host.querySelector(".maintenance-job");
      assert.equal(card.dataset.maintenanceJob, "fresh");
      assert.equal(card.querySelectorAll(".task-row").length, 1);
      assert.doesNotMatch(card.textContent, /TSMC|IMR/);
      assert.match(
        card.textContent,
        language === "en"
          ? /Found 3 · Included 1 · Skipped 2/
          : /发现 3 · 纳入 1 · 跳过 2/,
      );
      assert.match(card.textContent, /Inbox\/unpublished.md/);
      assert.match(
        card.textContent,
        language === "en" ? /not completed publication/ : /导入尚未完成发布/,
      );
      assert.match(card.textContent, /future_reason/);
      assert.match(
        card.textContent,
        language === "en"
          ? /Planned archive target: Resources\/EELS.md/
          : /计划归档目标：Resources\/EELS.md/,
      );
      assert.doesNotMatch(
        card.textContent,
        /任务记录归档位置|Recorded archive location/,
      );
      assert(
        buttons(card).some((text) => /确认并执行|Confirm and apply/.test(text)),
      );
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("preparing another task changes presentation only, and does not hide older execution markers", () => {
  const dom = installDOM();
  try {
    const preview = task({
        id: "preview",
        state: "preview",
        created_at: created + 10,
      }),
      running = task({
        id: "running",
        state: "running",
        created_at: created - 10,
      }),
      jobs = [running, preview],
      original = structuredClone(jobs),
      draft = {},
      host = dom.document.createElement("section"),
      form = dom.document.createElement("form"),
      renderJob = (job) => {
        const card = dom.document.createElement("article");
        card.dataset.maintenanceJob = job.id;
        return card;
      };
    const preparation = mountMaintenanceWorkspace(host, {
      jobs,
      draft,
      form,
      renderJob,
    });
    assert.equal(
      host.querySelector("article").dataset.maintenanceJob,
      "preview",
    );
    assert.match(host.textContent, /其他任务仍标为执行中：running/);
    preparation.open = true;
    preparation.listeners.toggle();
    assert.equal(host.querySelector("article"), null);
    assert.equal(draft.preparingFor, "preview");
    assert(host.querySelector('[data-view-key="job:preview"]'));
    assert.deepEqual(jobs, original);
    const endedHost = dom.document.createElement("section");
    mountMaintenanceWorkspace(endedHost, {
      jobs: [running, task()],
      draft: {},
      form,
      renderJob,
    });
    assert.equal(
      endedHost.querySelector("article").dataset.maintenanceJob,
      "834a4cf1",
    );
    assert.match(endedHost.textContent, /其他任务仍标为执行中：running/);
  } finally {
    dom.restore();
  }
});

test("an unexecuted preview or a reverted task never labels a target as a recorded archive", () => {
  const dom = installDOM();
  try {
    for (const extra of [
      { state: "preview", confirmed: false },
      { state: "cancelled", rollback_completed: true },
    ]) {
      const host = dom.document.createElement("details"),
        summary = dom.document.createElement("summary");
      host.append(summary);
      appendRecordedPaths(
        host,
        summary,
        note("Archive", { target: "Resources/Archive.md" }),
        task(extra),
      );
      assert.match(host.textContent, /计划归档目标：Resources\/Archive.md/);
      assert.doesNotMatch(host.textContent, /任务记录归档位置/);
    }
  } finally {
    dom.restore();
  }
});

test("empty confirmation errors describe the rejected operation in both languages", () => {
  try {
    setLocale("zh-CN");
    assert.match(
      String(msg("api.maintenance_no_actionable_candidates")),
      /没有可执行条目.*未确认执行/,
    );
    setLocale("en");
    assert.match(
      String(msg("api.maintenance_no_actionable_candidates")),
      /no executable items.*not confirmed/,
    );
  } finally {
    setLocale("zh-CN");
  }
});

test("known skip reasons are readable in both languages and incomplete statistics never claim full coverage", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const host = dom.document.createElement("section");
      appendCandidateCoverage(
        host,
        task({
          candidate_coverage: {
            complete: false,
            discovered_count: 6,
            included_count: 0,
            skipped_count: 6,
            skipped_truncated: true,
            skipped: [
              "expert_package_not_registered",
              "symbolic_link",
              "hidden_file",
              "vault_sync_cloud_pending",
              "vault_sync_source_changed",
              "vault_sync_cloud_metadata_unavailable",
              "__proto__",
            ].map((reason, i) => ({ path: `Inbox/${i}.md`, reason })),
          },
        }),
      );
      for (const pattern of language === "en"
        ? [
            /expert package is unregistered/,
            /Symbolic links are excluded/,
            /Hidden files are excluded/,
            /Local content is unavailable/,
            /Content changed/,
            /iCloud file status could not be verified/,
            /not confirmed complete/,
            /Only some recorded skipped items/,
          ]
        : [
            /专家包尚未登记/,
            /符号链接不参与整理/,
            /隐藏文件不参与整理/,
            /本地内容尚不可读/,
            /内容已变化/,
            /暂时无法核对 iCloud 文件状态/,
            /统计尚未确认完整/,
            /部分已记录的跳过资料/,
          ])
        assert.match(host.textContent, pattern);
      assert.match(host.textContent, /__proto__/);
      assert.doesNotMatch(
        host.textContent,
        /统计包含全部跳过项|counts include all skipped/,
      );
      assert.deepEqual(buttons(host), []);
      const partial = render([
        task({
          state: "partial",
          items: [note("pending", { state: "pending", phase: "pending" })],
        }),
      ]);
      assert.match(
        partial.textContent,
        language === "en" ? /Recorded target memberships/ : /任务记录目标归属/,
      );
      assert.doesNotMatch(
        partial.textContent,
        /任务记录整理后归属|Recorded memberships after organization/,
      );
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("all-skipped reports stay readable without confirmation, while explicit new MOCs stay confirmable", () => {
  const dom = installDOM();
  try {
    const report = task({
      state: "preview",
      confirmed: false,
      items: [],
      candidate_coverage: {
        discovered_count: 1,
        included_count: 0,
        skipped_count: 1,
        skipped: [
          { path: "Inbox/unpublished.md", reason: "publication_not_committed" },
        ],
      },
    });
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const host = render([report]);
      assert.match(
        host.textContent,
        language === "en"
          ? /This preview has no executable items/
          : /本次没有可执行条目/,
      );
      assert.match(host.textContent, /Inbox\/unpublished.md/);
      assert.match(
        host.querySelector(".record-heading").textContent,
        language === "en"
          ? /Candidate check · No executable items.*No confirmation required/
          : /候选检查 · 无可执行条目.*无需确认/,
      );
      assert.doesNotMatch(
        host.textContent,
        /等待确认|预览待确认|确认并执行|awaiting confirmation|waiting for confirmation|Confirm and apply/i,
      );
      assert(
        !buttons(host).some((text) =>
          /恢复未完成|Resume unfinished/.test(text),
        ),
      );
      const archivedReport = render([report], {
        preparingFor: report.id,
        mode: "quick",
        title: "",
        topic: "",
      });
      assert.match(
        archivedReport.querySelector(`[data-view-key="job:${report.id}"]`)
          .firstChild.textContent,
        language === "en" ? /No confirmation required/ : /无需确认/,
      );
      assert.doesNotMatch(
        archivedReport.textContent,
        /等待确认|预览待确认|awaiting confirmation|waiting for confirmation/i,
      );
      const mocOnly = render([
        { ...report, new_mocs: [{ id: "new", title: "New MOC" }] },
      ]);
      assert.match(
        mocOnly.textContent,
        language === "en"
          ? /Confirmation will only create the new MOCs/
          : /确认只会创建本预览列出的新 MOC/,
      );
      assert.match(
        mocOnly.querySelector(".record-heading").textContent,
        language === "en" ? /Preview awaiting confirmation/ : /预览待确认/,
      );
      assert(
        buttons(mocOnly).some((text) =>
          /确认并执行|Confirm and apply/.test(text),
        ),
      );
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("legacy coverage and absent counters remain unknown, and timestamp labels never infer completion", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const legacy = dom.document.createElement("section");
      appendCandidateCoverage(legacy, task({ candidate_coverage: undefined }));
      assert.match(
        legacy.textContent,
        language === "en" ? /did not record Inbox/ : /未记录 Inbox/,
      );
      assert.doesNotMatch(legacy.textContent, /跳过 0|Skipped 0/);
      const partial = dom.document.createElement("section");
      appendCandidateCoverage(
        partial,
        task({
          candidate_coverage: { discovered_count: 2, included_count: 1 },
        }),
      );
      assert.match(
        partial.textContent,
        language === "en" ? /Skipped Not recorded/ : /跳过 未记录/,
      );
      const record = String(
        taskRecordSummary(
          task({
            state: "completed",
            closed_at: undefined,
            updated_at: created + 5,
          }),
        ),
      );
      assert.match(record, language === "en" ? /Updated:/ : /更新：/);
      assert.doesNotMatch(record, /Completed:|Ended:|完成：|结束：/);
      assert.match(
        String(taskRecordSummary({ id: "legacy", state: "closed" })),
        language === "en" ? /Creation time was not recorded/ : /生成时间未记录/,
      );
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("completion stays visible across polling; only explicit preparation hides it and a fresh preview becomes current", async () => {
  const { previewSubmitted } =
    await import("../../src/fiber_bundle/web/organization_workspace.js");
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const older = task({
          id: "older-running",
          state: "running",
          created_at: created - 10,
        }),
        running = task({
          id: "latest",
          state: "running",
          created_at: created + 10,
        }),
        completed = {
          ...running,
          state: "completed",
          completed_at: created + 20,
          completed: 3,
        },
        draft = { mode: "quick", title: "", topic: "" };
      const paint = (jobs) => render(structuredClone(jobs), draft);
      const preparation = (host) =>
        host
          .querySelectorAll("details")
          .find((node) =>
            node.dataset.viewKey?.startsWith("maintenance-preparation:"),
          );
      let host = paint([older, running]);
      assert.equal(
        host.querySelector(".maintenance-job").dataset.maintenanceJob,
        "latest",
      );
      for (let poll = 0; poll < 3; poll++) {
        host = paint([
          older,
          { ...completed, updated_at: created + 21 + poll },
        ]);
        assert.equal(
          host.querySelector(".maintenance-job").dataset.maintenanceJob,
          "latest",
        );
        assert.match(
          host.querySelector(".record-heading").textContent,
          language === "en" ? /Complete/i : /完成/,
        );
        assert.match(host.textContent, /older-running/);
        assert.equal(preparation(host).open, false);
        assert.doesNotMatch(
          host.textContent,
          /正在准备新整理|Preparing a new organization/,
        );
        assert.equal(draft.preparingFor, undefined);
      }
      preparation(host).open = true;
      preparation(host).listeners.toggle();
      assert.equal(host.querySelector(".maintenance-job"), null);
      assert.equal(draft.preparingFor, "latest");
      assert(host.querySelector('[data-view-key="job:latest"]'));
      host = paint([older, completed]);
      assert.equal(preparation(host).open, true);
      assert.equal(host.querySelector(".maintenance-job"), null);
      assert.match(host.textContent, /older-running/);
      preparation(host).open = false;
      preparation(host).listeners.toggle();
      host = paint([older, completed]);
      assert.equal(preparation(host).open, false);
      assert.equal(
        host.querySelector(".maintenance-job").dataset.maintenanceJob,
        "latest",
      );
      preparation(host).open = true;
      preparation(host).listeners.toggle();
      previewSubmitted(draft);
      const fresh = task({
        id: "fresh-preview",
        state: "preview",
        confirmed: false,
        created_at: created + 30,
      });
      host = paint([older, completed, fresh]);
      assert.equal(
        host.querySelector(".maintenance-job").dataset.maintenanceJob,
        "fresh-preview",
      );
      assert.equal(preparation(host).open, false);
      assert.equal(draft.preparingFor, undefined);
      assert(host.querySelector('[data-view-key="job:latest"]'));
      assert(host.querySelector('[data-view-key="job:older-running"]'));
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});
