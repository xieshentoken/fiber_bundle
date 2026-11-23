import test from "node:test";
import assert from "node:assert/strict";
import { validIndexActivity } from "../../src/fiber_bundle/web/index_activity_api.js";

globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { createSession } = await import("../../src/fiber_bundle/web/session.js");
const { createAdminApi } = await import("../../src/fiber_bundle/web/api.js");
const {
  createIndexActivity,
  createIndexActivityMonitor,
  indexActivitySummary,
} = await import("../../src/fiber_bundle/web/index_activity.js");
const { setLocale } = await import("../../src/fiber_bundle/web/i18n/index.js");
const row = (state, extras = {}) => ({
  vault_id: "synthetic-vault",
  name: "我的知识库",
  state,
  pending_paths: 0,
  needs_attention: 0,
  updated_at: 100,
  ...extras,
});
const payload = (...vaults) => ({
  schema: "note-index-activity/v1",
  observed_at: 101,
  vaults,
});

test("index observations validate complete typed rows and never accept duplicate vaults or unknown states", () => {
  assert(validIndexActivity(payload()));
  assert(validIndexActivity(payload(row("building"))));
  assert(validIndexActivity(payload(row("queued", { phase: "packages" }))));
  assert(
    validIndexActivity(
      payload(row("unverified", { pending_paths: null, updated_at: null })),
    ),
  );
  for (const value of [
    {},
    { ...payload(), schema: "other" },
    { ...payload(), observed_at: NaN },
    { ...payload(), observed_at: null },
    { ...payload(), secret: "must not appear" },
    payload(row("unknown")),
    payload(row("queued", { phase: "unknown" })),
    payload(row("ready", { pending_paths: -1 })),
    payload(row("ready", { needs_attention: 0.5 })),
    payload(row("ready", { updated_at: "100" })),
    payload(row("ready", { name: "control\ntext" })),
    payload(row("ready", { path: "/private/source" })),
    payload(row("ready"), row("ready")),
  ])
    assert.equal(validIndexActivity(value), false, JSON.stringify(value));
});

test("the global summary distinguishes active, waiting, partial, failed and unconfirmed observations", () => {
  setLocale("zh-CN");
  const summarize = (...rows) =>
    indexActivitySummary({ value: payload(...rows) });
  assert.equal(summarize(row("ready")).state, "ready");
  assert.equal(summarize().state, "empty");
  assert.match(
    summarize(row("observing", { pending_paths: 8 })).label,
    /索引更新中.*1.*待检查 8/,
  );
  assert.equal(summarize(row("queued")).state, "updating");
  assert.equal(summarize(row("building")).state, "updating");
  assert.equal(summarize(row("waiting")).state, "waiting");
  assert.equal(summarize(row("failed")).state, "failed");
  assert.equal(summarize(row("needs_review")).state, "needs_review");
  assert.equal(summarize(row("unverified")).state, "unverified");
  assert.equal(
    indexActivitySummary({ value: payload(row("ready")), stale: true }).state,
    "unverified",
  );
  const mixed = summarize(
    row("observing", { pending_paths: 8 }),
    row("unverified", { vault_id: "other", pending_paths: null }),
  );
  assert.equal(mixed.state, "updating");
  assert.doesNotMatch(mixed.label, /待检查/); // Unknown counts are not displayed as a total.
  const mixedFailure = summarize(
    row("building"),
    row("failed", { vault_id: "failed-vault" }),
  );
  assert.equal(mixedFailure.state, "failed");
  assert.match(mixedFailure.label, /索引更新中.*1 个库.*1 个库失败/);
  setLocale("en");
  assert.match(summarize(row("observing")).label, /Updating indexes/);
  assert.match(summarize(row("waiting")).label, /Indexes waiting/);
  assert.match(summarize(row("needs_review")).label, /partially available/);
  setLocale("zh-CN");
});

test("activity polling serializes reads, repeats only after completion, and stops its timer", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0,
    finish;
  const observations = [];
  const monitor = createIndexActivityMonitor(
    () => {
      calls++;
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
    (value) => observations.push(value),
  );
  monitor.start();
  const first = monitor.refresh();
  assert.equal(first, monitor.refresh());
  await Promise.resolve();
  t.mock.timers.tick(60_000);
  assert.equal(calls, 1);
  finish(payload(row("observing", { pending_paths: 3 })));
  await first;
  t.mock.timers.tick(14_999);
  assert.equal(calls, 1);
  t.mock.timers.tick(1);
  const second = monitor.refresh();
  await Promise.resolve();
  assert.equal(calls, 2);
  finish(payload(row("ready")));
  await second;
  assert.equal(observations.at(-1).value.vaults[0].state, "ready");
  monitor.stop();
  t.mock.timers.tick(60_000);
  assert.equal(calls, 2);
});

test("a read failure retains the prior result with an explicit stale state and recovers on success", async () => {
  let fail = false;
  const observations = [];
  const monitor = createIndexActivityMonitor(
    async () => {
      if (fail) throw Error("synthetic local metadata unavailable");
      return payload(row("ready"));
    },
    (value) => observations.push(value),
  );
  await monitor.refresh();
  const last = observations.at(-1).value;
  fail = true;
  await monitor.refresh();
  assert.equal(observations.at(-1).value, last);
  assert.equal(observations.at(-1).stale, true);
  assert.equal(indexActivitySummary(observations.at(-1)).state, "unverified");
  fail = false;
  await monitor.refresh();
  assert.equal(observations.at(-1).stale, false);
  assert.equal(indexActivitySummary(observations.at(-1)).state, "ready");
  monitor.stop({ clear: true });
  assert.equal(observations.at(-1), null);
});

test("logout or hiding aborts a read and prevents a late result from replacing the resumed observation", async () => {
  const reads = [],
    observations = [];
  const monitor = createIndexActivityMonitor(
    ({ signal }) =>
      new Promise((resolve) => {
        reads.push({ signal, resolve });
      }),
    (value) => observations.push(value),
  );
  const old = monitor.refresh();
  await Promise.resolve();
  monitor.stop({ clear: true });
  assert.equal(reads[0].signal.aborted, true);
  const resumed = monitor.refresh();
  await Promise.resolve();
  reads[1].resolve(payload(row("building")));
  await resumed;
  const newer = observations.at(-1);
  reads[0].resolve(payload(row("ready")));
  await old;
  assert.equal(observations.at(-1), newer);
  assert.equal(indexActivitySummary(newer).state, "updating");
  monitor.stop({ clear: true });
});

test("invalid activity payloads remain unconfirmed rather than reporting ready", async () => {
  const observations = [];
  const monitor = createIndexActivityMonitor(
    async () => ({ state: "ready" }),
    (value) => observations.push(value),
  );
  await monitor.refresh();
  assert.equal(observations.at(-1).stale, true);
  assert.equal(observations.at(-1).value, null);
  monitor.stop();
});

test("the activity API is one cancellable read and does not change the independent connection indicator", async () => {
  const connections = [],
    calls = [],
    controller = new AbortController();
  let fail = false;
  const api = createAdminApi(
    createSession(),
    {
      onConnection: (value) => connections.push(value),
      onUnauthorized() {},
    },
    async (path, options) => {
      calls.push({ path, options });
      if (fail) throw Error("synthetic network failure");
      return { ok: true, json: async () => payload(row("ready")) };
    },
  );
  assert(
    validIndexActivity(await api.indexActivity({ signal: controller.signal })),
  );
  assert.equal(calls[0].path, "/admin/note-index/activity");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert(calls[0].options.signal instanceof globalThis.AbortSignal);
  fail = true;
  await assert.rejects(api.indexActivity());
  assert.equal(calls.length, 2);
  assert.deepEqual(connections, []);
});

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.events = {};
    this.dataset = {};
    this.hidden = false;
  }
  set textContent(value) {
    this.children = [];
    this.text = value;
  }
  get textContent() {
    return this.text + this.children.map((child) => child.textContent).join("");
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.text = "";
    this.children = children;
  }
  addEventListener(name, callback) {
    this.events[name] = callback;
  }
}
test("the disclosure renders per-vault stages and stale evidence, translates UI labels, and opens only the chosen vault", async () => {
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: (tag) => new Element(tag),
    documentElement: {},
  };
  let fail = false;
  const opened = [];
  const view = createIndexActivity({
    read: async () => {
      if (fail) throw Error("synthetic local metadata unavailable");
      return payload(
        row("observing", { pending_paths: 8, needs_attention: 2 }),
      );
    },
    onOpen: (id) => opened.push(id),
  });
  try {
    assert.equal(view.node.hidden, true);
    await view.refresh();
    assert.equal(view.node.hidden, false);
    assert.match(view.node.textContent, /待检查 8 个路径/);
    assert.match(view.node.textContent, /需核对 2 项/);
    const panel = view.node.children[1],
      item = panel.children.at(-1),
      button = item.children.at(-1);
    view.node.open = true;
    button.events.click();
    assert.deepEqual(opened, ["synthetic-vault"]);
    assert.equal(view.node.open, false);
    fail = true;
    await view.refresh();
    assert.match(view.node.textContent, /上次观察于/);
    assert.match(view.node.textContent, /当前状态尚未确认/);
    assert.equal(view.node.dataset.state, "unverified");
    setLocale("en");
    assert.match(view.node.textContent, /Index status unavailable/);
    assert.match(view.node.textContent, /Last observed at/);
    assert.match(view.node.textContent, /我的知识库/); // Source names are never translated.
    view.stop({ clear: true });
    assert.equal(view.node.hidden, true);
    assert.equal(view.node.children[1].children.length, 0);
  } finally {
    view.dispose();
    setLocale("zh-CN");
    globalThis.document = originalDocument;
  }
});
