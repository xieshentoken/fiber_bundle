import test from "node:test";
import assert from "node:assert/strict";
globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { setLocale } = await import("../../src/fiber_bundle/web/i18n/index.js");
const {
  archiveReadiness,
  appendArchivePreview,
  appendArchiveIssue,
  appendArchiveRecovery,
} =
  await import("../../src/fiber_bundle/web/organization_archive_readiness.js");
const { itemStages } =
  await import("../../src/fiber_bundle/web/organization_stages.js");
const { maintenancePhase } =
  await import("../../src/fiber_bundle/web/organization_progress.js");

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.listeners = {};
    this.textContent = "";
  }
  append(...children) {
    this.children.push(...children);
  }
  prepend(...children) {
    this.children.unshift(...children);
  }
  replaceChildren(...children) {
    this.children = children;
  }
  addEventListener(name, handler) {
    this.listeners[name] = handler;
  }
  setAttribute(name, value) {
    this[name] = value;
  }
  removeAttribute(name) {
    delete this[name];
  }
  closest() {
    return null;
  }
}
function mount() {
  const previous = globalThis.document;
  globalThis.document = {
    createElement: (tag) => new Element(tag),
    documentElement: {},
    getElementById: () => ({ dataset: {} }),
  };
  return {
    host: new Element("section"),
    restore: () => {
      globalThis.document = previous;
    },
  };
}
const blocked = {
  entity: "bad",
  path: "Inbox/figure.md",
  target: "Resource/figure.md",
  after: ["moc"],
  status: "linked",
  archive_preflight: {
    state: "blocked",
    reason: "maintenance_archive_link_review_required",
    links: [
      { note_path: "Inbox/figure.md", line: 7, column: 11, target: "上" },
    ],
  },
};

test("preview totals distinguish mounting and archiving and show raw link locations in both languages", () => {
  const job = {
    mode: "quick",
    items: [blocked, { ...blocked, archive_preflight: { state: "ready" } }],
  };
  assert.deepEqual(archiveReadiness(job), {
    memberships: 2,
    ready: 1,
    blocked: 1,
  });
  for (const locale of ["zh-CN", "en"]) {
    const { host, restore } = mount();
    try {
      setLocale(locale);
      appendArchivePreview(host, job);
      appendArchiveIssue(host, blocked);
      assert.match(
        host.children[0].textContent,
        locale === "en"
          ? /Ready within selected scope: 1.*deferred: 1/
          : /可归档 1.*暂缓归档 1/,
      );
      const link = host.children.at(-1).children[0].textContent;
      assert.match(link, /Inbox\/figure.md/);
      assert.match(
        link,
        locale === "en" ? /Line 7.*Link: 上/ : /第 7 行.*链接：上/,
      );
      assert.equal(itemStages(blocked, job).archive, "归档暂缓待核对");
      assert.equal(
        maintenancePhase({
          ...job,
          state: "waiting_file",
          items: [{ ...blocked, archive_wait: blocked.archive_preflight }],
        }),
        "部分已完成 · 归档待核对",
      );
    } finally {
      restore();
    }
  }
  setLocale("zh-CN");
});

test("legacy recovery inspection is read-only and confirmation sends the fresh proof only after consent", async () => {
  const { host, restore } = mount();
  const calls = [],
    refreshed = [];
  const job = {
    id: "old",
    state: "partial",
    confirmed: true,
    items: [
      {
        ...blocked,
        phase: "members_done",
        archive_kind: "managed",
        archive_operation: "original",
      },
    ],
  };
  const api = {
    maintenanceAction: async (...args) => {
      calls.push(args);
      return {
        archive_deferral_preview: {
          plan_sha256: "proof",
          items: [
            {
              path: blocked.path,
              target: blocked.target,
              links: blocked.archive_preflight.links,
            },
          ],
        },
      };
    },
  };
  try {
    appendArchiveRecovery(host, job, { vault_id: "v" }, api, async (options) =>
      refreshed.push(options),
    );
    assert.deepEqual(calls, []);
    const panel = host.children[0];
    await panel.children[0].listeners.click();
    assert.equal(calls.length, 1);
    assert.equal(calls[0][1], "defer_archive_preview");
    const button = panel.children.at(-1),
      checkbox = panel.children.at(-2).children[0];
    assert.equal(button.disabled, true);
    checkbox.checked = true;
    checkbox.listeners.change();
    assert.equal(button.disabled, false);
    await button.listeners.click();
    assert.deepEqual(calls[1], [
      "old",
      "defer_archives",
      { vault_id: "v", plan_sha256: "proof" },
    ]);
    assert.deepEqual(refreshed, [{ automatic: true }]);
    assert.equal(job.items[0].archive_operation, "original");
  } finally {
    restore();
  }
});

test("a retained prepared receipt offers no repeated deferral action", () => {
  const { host, restore } = mount();
  try {
    appendArchiveRecovery(
      host,
      {
        state: "waiting_file",
        confirmed: true,
        items: [
          {
            phase: "members_done",
            archive_operation: "original",
            archive_kind: "managed",
            archive_deferral: {},
          },
        ],
      },
      {},
      {},
      () => {},
    );
    assert.equal(host.children.length, 0);
  } finally {
    restore();
  }
});

test("the ordinary resume action cannot requeue an immutable not-ready or expired archive", async () => {
  const { taskActions, archiveResumeNeedsReview } =
    await import("../../src/fiber_bundle/web/organization_task_controls.js");
  for (const error of [
    "placement_preview_not_ready",
    "placement_preview_expired",
  ]) {
    const job = {
      state: "partial",
      confirmed: true,
      error,
      items: [
        {
          ...blocked,
          phase: "members_done",
          archive_operation: "original",
          error,
        },
      ],
    };
    assert.equal(archiveResumeNeedsReview(job), true);
    assert.equal(
      taskActions(job).some((spec) => spec.operation === "resume"),
      false,
    );
  }
  assert.equal(
    taskActions({
      state: "partial",
      confirmed: true,
      items: [{ phase: "members_done", archive_operation: "ready" }],
    }).some((spec) => spec.operation === "resume"),
    true,
  );
});

test("bounded external dependency failures identify source paths in English", async () => {
  const { appendArchiveDependencyFailure } =
    await import("../../src/fiber_bundle/web/organization_archive_readiness.js");
  const { host, restore } = mount();
  try {
    setLocale("en");
    appendArchiveDependencyFailure(host, {
      reason: "maintenance_archive_dependency_unavailable",
      issues: [{ path: "Resource/private.md", reason: "source_budget" }],
    });
    assert.match(host.children[0].textContent, /execution is not confirmed/);
    assert.match(
      host.children[1].children[0].textContent,
      /Resource\/private.md: Changed sources exceed/,
    );
  } finally {
    restore();
    setLocale("zh-CN");
  }
});
