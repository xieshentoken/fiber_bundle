import test from "node:test";
import assert from "node:assert/strict";
import { installDOM } from "./reliability_dom_fixture.js";
globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { appendInboxReview, appendInboxReviewHistory } =
  await import("../../src/fiber_bundle/web/organization_inbox_review.js");
const { createInboxReviewApi } =
  await import("../../src/fiber_bundle/web/organization_inbox_review_api.js");
const { maintenanceSection } =
  await import("../../src/fiber_bundle/web/organization_maintenance.js");
const { organizationDiagnostic } =
  await import("../../src/fiber_bundle/web/organization_diagnostics.js");
const { setLocale } = await import("../../src/fiber_bundle/web/i18n/index.js");
const fixed = { client_id: "c", vault_id: "v" };
const job = {
  id: "j",
  maintenance: true,
  mode: "quick",
  state: "preview",
  confirmed: false,
  items: [],
  new_mocs: [],
};
const source = 'caption [110](上) <script>alert("x")</script>';
const inspection = (extra = {}) => ({
  path: "Inbox/n.md",
  byte_count: 78,
  sha256: "sha",
  classification: "content",
  text: {
    lines: [{ number: 42, text: source }],
    truncated: true,
    max_bytes: 65536,
  },
  actions: { unlink_missing: true },
  links: [
    {
      id: "link-1",
      line: 42,
      target: "上",
      before: source,
      after: 'caption \\[110\\](上) <script>alert("x")</script>',
    },
  ],
  ...extra,
});
const operation = (extra = {}) => ({
  operation_id: "op",
  state: "prepared",
  action: "unlink_missing",
  path: "Inbox/n.md",
  plan_sha256: "seal",
  changes: [{ line: 42, before: source, after: "plain" }],
  can_apply: true,
  ...extra,
});
const button = (host, text) =>
  host
    .querySelectorAll("button")
    .find((node) => node.textContent.includes(text));
async function click(host, text) {
  const node = button(host, text);
  assert(node, text);
  assert.equal(node.disabled, false, text);
  await node.listeners.click();
}
function check(input, checked = true) {
  input.checked = checked;
  input.listeners.change();
}
function setup(api = {}) {
  const dom = installDOM(),
    calls = [],
    draft = {},
    context = {
      fixed,
      job,
      path: "Inbox/n.md",
      draft,
      refresh: async () => calls.push(["refresh"]),
      api: {
        maintain: async (body) => calls.push(["maintain", body]),
        inboxReview: {
          inspect: async (body) => {
            calls.push(["inspect", body]);
            return inspection();
          },
          preview: async (body) => {
            calls.push(["preview", body]);
            return operation();
          },
          apply: async (id, body) => {
            calls.push(["apply", id, body]);
            return operation({ state: "completed", can_apply: false });
          },
          ...api,
        },
      },
    };
  const render = () => {
    const host = dom.document.createElement("section");
    appendInboxReview(host, context);
    return host;
  };
  return { dom, calls, context, render };
}

test("review transport preserves scope and sealed operations on fixed authenticated routes", async () => {
  const calls = [],
    api = createInboxReviewApi(async (...args) => calls.push(args.slice(0, 3)));
  await api.inspect({ ...fixed, path: "Inbox/n.md", job_id: "j" });
  await api.preview({ ...fixed, action: "quarantine_empty" });
  await api.apply("op/a", { ...fixed, expected_plan_sha256: "seal" });
  await api.restorePreview("op/a", fixed);
  await api.history(fixed);
  assert.equal(calls[0][0], "/admin/organization/inbox-review/inspect");
  assert.equal(calls[0][2].job_id, "j");
  assert.equal(calls[1][2].action, "quarantine_empty");
  assert.deepEqual(calls[2], [
    "/admin/organization/inbox-review/operations/op%2Fa/apply",
    "POST",
    { ...fixed, expected_plan_sha256: "seal" },
  ]);
  assert.equal(
    calls[3][0],
    "/admin/organization/inbox-review/operations/op%2Fa/restore-preview",
  );
  assert.equal(
    calls[4][0],
    "/admin/organization/inbox-review/history?client_id=c&vault_id=v",
  );
});

test("inspect uses current bounded plain text and selected link diff survives repaints until sealed confirmation", async () => {
  const { dom, context, calls, render } = setup();
  try {
    let host = render();
    await click(host, "重新检查");
    assert.match(host.textContent, /当前大小：78 字节/);
    assert.match(host.textContent, /65536/);
    assert.equal(host.querySelectorAll("script").length, 0);
    assert(
      host.querySelector(".inbox-review-text").textContent.includes(source),
    );
    check(host.querySelector('[data-link-id="link-1"]'));
    host = render();
    assert.equal(host.querySelector('[data-link-id="link-1"]').checked, true);
    await click(host, "预览所选");
    assert.deepEqual(calls.find(([name]) => name === "preview")[1], {
      ...fixed,
      job_id: "j",
      path: "Inbox/n.md",
      action: "unlink_missing",
      link_ids: ["link-1"],
    });
    assert.equal(button(host, "确认执行").disabled, true);
    check(host.querySelector('[data-review-consent="op"]'));
    host = render();
    assert.equal(
      host.querySelector('[data-review-consent="op"]').checked,
      true,
    );
    await click(host, "确认执行");
    assert.deepEqual(
      calls.find(([name]) => name === "apply"),
      ["apply", "op", { ...fixed, expected_plan_sha256: "seal" }],
    );
    assert(!calls.some(([name]) => name === "maintain"));
    assert.match(host.textContent, /文件处理已完成/);
    await click(host, "生成快速预览");
    assert.deepEqual(calls.find(([name]) => name === "maintain")[1], {
      ...fixed,
      mode: "quick",
      unavailable_action: "moc_only",
      new_mocs: [],
    });
    assert.equal(context.draft.previewVersion, 1);
    assert.equal(calls.filter(([name]) => name === "apply").length, 1);
  } finally {
    dom.restore();
  }
});

test("empty quarantine needs confirmation; unreadable content offers no destructive operation", async () => {
  for (const classification of ["empty", "unreadable", "content"]) {
    const { dom, render, calls } = setup({
      inspect: async () =>
        inspection({
          classification,
          actions: { quarantine_empty: classification === "empty" },
          links: [],
          text: { lines: [], truncated: false },
        }),
      preview: async () =>
        operation({ action: "quarantine_empty", changes: [] }),
    });
    try {
      const host = render();
      await click(host, "重新检查");
      if (classification !== "empty") {
        assert.equal(button(host, "隔离"), undefined);
        continue;
      }
      await click(host, "预览可恢复隔离");
      assert.match(host.textContent, /只移动这一篇空文件/);
      assert.match(host.textContent, /附件、来源对象、其他版本和历史保留/);
      assert(!calls.some(([name]) => name === "apply"));
      assert.equal(button(host, "确认执行").disabled, true);
    } finally {
      dom.restore();
    }
  }
});

test("backend failure remains specific and is never silently retried or described as unfinished organization recovery", async () => {
  const { dom, render, calls } = setup({
    preview: async () => {
      const error = new Error("changed");
      error.code = "inbox_review_file_changed";
      error.error_detail = {
        path: "Inbox/n.md",
        reason: "file_digest_changed",
      };
      throw error;
    },
  });
  try {
    const host = render();
    await click(host, "重新检查");
    check(host.querySelector('[data-link-id="link-1"]'));
    await click(host, "预览所选");
    assert.match(host.textContent, /file_digest_changed/);
    assert.match(host.textContent, /Inbox\/n.md/);
    assert.doesNotMatch(host.textContent, /恢复未完成整理/);
    assert(!calls.some(([name]) => name === "apply"));
    assert.equal(button(host, "确认执行"), undefined);
    assert.doesNotMatch(
      String(
        organizationDiagnostic({
          ...job,
          error: "note directory changed",
          error_detail: { reason: "directory_changed" },
        }).text,
      ),
      /恢复/,
    );
  } finally {
    dom.restore();
  }
});

test("history restore is a separate preview with a new confirmation and original operation preserved", async () => {
  const calls = [],
    { dom, context } = setup({
      history: async () => ({
        operations: [
          operation({
            state: "completed",
            can_apply: false,
            can_restore: true,
          }),
        ],
      }),
      restorePreview: async (id, scope) => {
        calls.push([id, scope]);
        return operation({ operation_id: "restore", action: "restore_links" });
      },
    });
  try {
    const host = dom.document.createElement("section");
    appendInboxReviewHistory(host, context);
    await click(host, "读取处理历史");
    await click(host, "预览恢复");
    assert.deepEqual(calls, [["op", fixed]]);
    assert.match(host.textContent, /restore/);
    assert.match(host.textContent, /处理记录：op/);
    assert.equal(button(host, "确认执行").disabled, true);
    check(host.querySelector('[data-review-consent="restore"]'));
    const next = dom.document.createElement("section");
    appendInboxReviewHistory(next, context);
    assert.equal(
      next.querySelector('[data-review-consent="restore"]').checked,
      true,
    );
  } finally {
    dom.restore();
  }
});

test("candidate and skipped entries share inspection in both languages and retain manual MOC controls", () => {
  const { dom, context } = setup();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const host = maintenanceSection(
        {
          mocs: [],
          jobs: [
            {
              ...job,
              created_at: 1,
              stage: "preview",
              candidate_coverage: {
                discovered_count: 2,
                included_count: 1,
                skipped_count: 1,
                skipped: [
                  {
                    path: "Inbox/empty.md",
                    reason: "publication_not_committed",
                  },
                ],
              },
              items: [
                {
                  entity: "n",
                  title: "n",
                  path: "Inbox/n.md",
                  evaluated: true,
                  before: [],
                  after: [],
                  add: [],
                  remove: [],
                },
              ],
            },
          ],
        },
        fixed,
        context.api,
        context.refresh,
        context.draft,
      );
      assert.equal(host.querySelectorAll(".inbox-review").length, 3);
      assert.match(
        host.textContent,
        language === "en" ? /Inspect and resolve/ : /检查与处理/,
      );
      assert.match(
        host.textContent,
        language === "en"
          ? /Confirm and apply this organization|Confirm and apply/
          : /确认并执行本次整理/,
      );
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("authenticated HTTP errors retain only safe review evidence and give preview-specific confirmation guidance", async () => {
  const { createSession } =
    await import("../../src/fiber_bundle/web/session.js");
  const { createAdminApi } = await import("../../src/fiber_bundle/web/api.js");
  let requests = 0;
  const api = createAdminApi(
    createSession(),
    { onConnection() {}, onUnauthorized() {} },
    async () => {
      requests++;
      return {
        ok: false,
        status: 409,
        json: async () => ({
          error: "inbox_review_restore_path_occupied",
          error_detail: {
            reason: "restore_path_occupied",
            path: "Inbox/n.md",
            next_step: "resolve_conflict",
            private_token: "never",
          },
        }),
      };
    },
  );
  await assert.rejects(
    api.organization.inboxReview.apply("op", {
      ...fixed,
      expected_plan_sha256: "seal",
    }),
    (error) => {
      assert.deepEqual(error.diagnostic, {
        reason: "restore_path_occupied",
        path: "Inbox/n.md",
        next_step: "resolve_conflict",
      });
      return true;
    },
  );
  assert.equal(requests, 1);
  const confirm = createAdminApi(
    createSession(),
    { onConnection() {}, onUnauthorized() {} },
    async () => ({
      ok: false,
      status: 409,
      json: async () => ({
        error: "placement_link_sources_changed",
        error_detail: {
          reason: "directory_changed",
          phase: "archive_link_census",
          path: ".",
          changed_fields: ["mtime_ns", "ctime_ns"],
        },
      }),
    }),
  );
  await assert.rejects(
    confirm.organization.maintenanceAction("j", "confirm", fixed),
    (error) => {
      assert.match(error.message, /尚未执行整理/);
      assert.doesNotMatch(error.message, /恢复/);
      assert.match(error.message, /archive_link_census/);
      return true;
    },
  );
});

test("malformed operation and inspection responses are rejected at the transport boundary", async () => {
  const { createSession } =
    await import("../../src/fiber_bundle/web/session.js");
  const { createAdminApi } = await import("../../src/fiber_bundle/web/api.js");
  for (const malformed of [
    { action: "unlink_missing", state: "prepared", can_apply: "true" },
    {
      ...inspection(),
      text: { lines: [{ number: -1, text: "bad" }], truncated: false },
    },
  ]) {
    const api = createAdminApi(
      createSession(),
      { onConnection() {}, onUnauthorized() {} },
      async () => ({ ok: true, json: async () => malformed }),
    );
    const call = malformed.action
      ? api.organization.inboxReview.preview(fixed)
      : api.organization.inboxReview.inspect(fixed);
    await assert.rejects(call, /后台响应格式无效/);
  }
});

test("review writes stay locked across repaints and a failed write requires fresh history rather than a repeated old confirmation", async () => {
  let finish,
    writes = 0;
  const { dom, render } = setup({
    apply: async () => {
      writes++;
      return new Promise((resolve, reject) => {
        finish = () => {
          const error = new Error("changed");
          error.code = "inbox_review_stage_changed";
          reject(error);
        };
      });
    },
  });
  try {
    let host = render();
    await click(host, "重新检查");
    check(host.querySelector('[data-link-id="link-1"]'));
    await click(host, "预览所选");
    check(host.querySelector('[data-review-consent="op"]'));
    const pending = click(host, "确认执行");
    host = render();
    assert.equal(button(host, "确认执行").disabled, true);
    assert.match(host.textContent, /后台正在执行已确认/);
    finish();
    await pending;
    assert.equal(writes, 1);
    assert.equal(button(host, "确认执行"), undefined);
    assert.match(host.textContent, /阶段已变化/);
    assert.match(host.textContent, /读取处理历史/);
  } finally {
    dom.restore();
  }
});

test("restore conflicts have readable next steps in both languages and technical disclosure state survives repaint", async () => {
  const { appendReviewFailure } =
    await import("../../src/fiber_bundle/web/organization_inbox_review_view.js");
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      for (const reason of [
        "restore_chain_invalid",
        "restore_path_occupied",
        "restore_file_changed",
        "plan_changed",
        "stage_changed",
        "concurrent_edit_preserved",
        "concurrent_edit_restore_required",
        "publication_changed",
        "registration_changed",
        "already_restored",
      ]) {
        const state = {},
          host = dom.document.createElement("section"),
          error = {
            code: "inbox_review_" + reason,
            diagnostic: { reason, path: "Inbox/n.md" },
          };
        appendReviewFailure(host, error, state);
        const visible = host.children
          .filter((node) => node.tagName !== "DETAILS")
          .map((node) => node.textContent)
          .join(" ");
        assert(!visible.includes(reason), visible);
        if (reason === "restore_chain_invalid") {
          assert.match(
            visible,
            language === "en" ? /current files are preserved/i : /现有文件保留/,
          );
          assert.match(
            visible,
            language === "en" ? /Read operation history/i : /读取处理历史/,
          );
        }
        if (reason === "concurrent_edit_restore_required") {
          assert.doesNotMatch(visible, /隔离|空文件|quarantine|empty.file/i);
          assert.match(
            visible,
            language === "en"
              ? /saved file.*original path/i
              : /保存的文件与原路径/,
          );
        }
        const details = host.querySelector("details");
        details.open = true;
        details.listeners.toggle();
        const next = dom.document.createElement("section");
        appendReviewFailure(next, error, state);
        assert.equal(next.querySelector("details").open, true);
      }
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("ordinary confirmation remains explicit, keeps its checkbox and pending request across polling replacements", async () => {
  const dom = installDOM(),
    draft = { mode: "quick", title: "", topic: "" },
    calls = [];
  let finish;
  const task = {
    ...job,
    created_at: 1,
    plan_sha256: "current-seal",
    stage: "preview",
    items: [
      {
        entity: "n",
        title: "n",
        path: "Inbox/n.md",
        evaluated: true,
        before: [],
        after: [],
        add: [],
        remove: [],
      },
    ],
  };
  const api = {
    maintenanceAction: async () => {
      calls.push("confirm");
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
  };
  const render = () =>
    maintenanceSection(
      { jobs: [task], mocs: [] },
      fixed,
      api,
      async () => {},
      draft,
    );
  try {
    let host = render(),
      confirm = button(host, "确认并执行");
    assert.equal(confirm.disabled, true);
    const notice = dom.document.createElement("p");
    notice.id = "notice";
    dom.document.body.append(notice);
    const consent = host.querySelector('[data-maintenance-consent="j"]');
    check(consent);
    host = render();
    confirm = button(host, "确认并执行");
    assert.equal(confirm.disabled, false);
    assert.equal(
      host.querySelector('[data-maintenance-consent="j"]').checked,
      true,
    );
    const pending = confirm.listeners.click();
    host = render();
    assert.equal(button(host, "确认并执行").disabled, true);
    assert.match(host.textContent, /正在等待后台核对本次确认/);
    assert.doesNotMatch(host.textContent, /后台正在归档/);
    finish();
    await pending;
    assert.deepEqual(calls, ["confirm"]);
    assert.equal(button(host, "确认并执行").disabled, false);
  } finally {
    dom.restore();
  }
});

test("completed processing links to the current vault index without initiating indexing or applying organization", async () => {
  const { dom, context, render, calls } = setup();
  const destinations = [];
  context.draft.onOpenIndex = (id) => destinations.push(id);
  try {
    const host = render();
    await click(host, "重新检查");
    check(host.querySelector('[data-link-id="link-1"]'));
    await click(host, "预览所选");
    check(host.querySelector('[data-review-consent="op"]'));
    await click(host, "确认执行");
    await click(host, "查看此知识库索引");
    assert.deepEqual(destinations, ["v"]);
    assert(!calls.some(([name]) => name === "maintain"));
  } finally {
    dom.restore();
  }
});

test("restore-only reconciliation offers restoration without continuing the stale empty-file plan", async () => {
  const { dom, context, render } = setup({
    apply: async () =>
      operation({
        action: "quarantine_empty",
        state: "reconcile",
        can_apply: false,
        can_restore: true,
        can_reconcile: false,
        error: "inbox_review_concurrent_edit_preserved",
      }),
    restorePreview: async () =>
      operation({
        operation_id: "restore-new-body",
        action: "restore_quarantine",
      }),
  });
  try {
    const host = render();
    await click(host, "重新检查");
    check(host.querySelector('[data-link-id="link-1"]'));
    await click(host, "预览所选");
    check(host.querySelector('[data-review-consent="op"]'));
    await click(host, "确认执行");
    assert.equal(button(host, "核对并完成"), undefined);
    assert.match(host.textContent, /保留现有修改/);
    assert.match(host.textContent, /预览恢复此处理/);
    await click(host, "预览恢复此处理");
    assert.equal(
      host.querySelector('[data-review-consent="restore-new-body"]').checked,
      false,
    );
    assert.equal(button(host, "确认执行").disabled, true);
    assert.equal(
      context.draft.inboxReview.panels.values().next().value.plan.action,
      "restore_quarantine",
    );
  } finally {
    dom.restore();
  }
});

test("scope changes and revoked diagnostics cannot redisplay another scope's cached source", async () => {
  const { dom, context, render } = setup();
  try {
    let host = render();
    await click(host, "重新检查");
    assert.match(host.textContent, /caption/);
    context.fixed = { ...fixed, vault_id: "other" };
    host = render();
    assert.doesNotMatch(host.textContent, /caption/);
    context.api.inboxReview.inspect = async () => {
      const error = new Error("denied");
      error.status = 403;
      throw error;
    };
    await click(host, "重新检查");
    assert.doesNotMatch(host.textContent, /caption/);
    assert.equal(button(host, "隔离"), undefined);
  } finally {
    dom.restore();
  }
});
