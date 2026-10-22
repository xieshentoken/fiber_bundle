import test from "node:test";
import assert from "node:assert/strict";
import { createConnectionMonitor } from "../../src/fiber_bundle/web/atlas/connection.js";
import { visibleVault } from "../../src/fiber_bundle/web/atlas/vault_visibility.js";
import { createStore } from "../../src/fiber_bundle/web/atlas/store.js";

test("health failures are confirmed, recover, and concurrent probes coalesce", async () => {
  const states = [];
  let fail = false,
    pending,
    calls = 0;
  const monitor = createConnectionMonitor(
    async () => {
      calls++;
      if (fail) throw Error("synthetic health failure");
      if (pending) return pending;
      return { status: "ok" };
    },
    (state) => states.push(state),
  );
  await monitor.refresh();
  fail = true;
  await monitor.refresh();
  await monitor.refresh();
  fail = false;
  let finish;
  pending = new Promise((resolve) => {
    finish = resolve;
  });
  const first = monitor.refresh(),
    second = monitor.refresh();
  assert.equal(first, second);
  await Promise.resolve();
  finish({ status: "ok" });
  await first;
  assert.equal(calls, 4);
  assert.deepEqual(states, [
    "connected",
    "checking",
    "disconnected",
    "connected",
  ]);
  monitor.stop();
});

test("stopped probes cannot overwrite a new connection observation", async () => {
  const states = [];
  let finish;
  const monitor = createConnectionMonitor(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
    (state) => states.push(state),
  );
  const pending = monitor.refresh();
  await Promise.resolve();
  monitor.stop();
  finish({ status: "ok" });
  await pending;
  assert.deepEqual(states, []);
});

test("invalid health payload never reports connected", async () => {
  const states = [];
  const monitor = createConnectionMonitor(
    async () => ({}),
    (state) => states.push(state),
  );
  await monitor.refresh();
  await monitor.refresh();
  assert.deepEqual(states, ["checking", "disconnected"]);
  monitor.stop();
});

test("removed vault registrations are hidden even when legacy status says active", async () => {
  const rows = [
    { id: "current", status: "active", attachment_state: "attached" },
    { id: "personal", status: "active", attachment_state: "trashed" },
    { id: "supplier", status: "detached" },
    { id: "reconcile", status: "reconcile", attachment_state: "reconcile" },
  ];
  assert.deepEqual(
    rows.filter(visibleVault).map((v) => v.id),
    ["current", "reconcile"],
  );
  const store = createStore({ vaults: async () => rows });
  assert.deepEqual(
    (await store.vaults()).map((v) => v.id),
    ["current", "reconcile"],
  );
  rows[0].attachment_state = "detached";
  store.invalidate("vaults");
  assert.deepEqual(
    (await store.vaults()).map((v) => v.id),
    ["reconcile"],
  );
  assert.equal(rows.length, 4); // Historical registrations were not deleted.
});

test("Atlas and shared components use one locale, including failed switch preparation", async () => {
  const data = new Map();
  globalThis.localStorage = {
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => data.set(k, v),
  };
  const { setLocale, locale } =
    await import("../../src/fiber_bundle/web/i18n/index.js");
  const { lang, strings } =
    await import("../../src/fiber_bundle/web/atlas/i18n.js");
  const { switchLocale } =
    await import("../../src/fiber_bundle/web/atlas/locale_switch.js");
  const t = strings({
    zh: { title: "选择笔记" },
    en: { title: "Choose notes" },
  });
  setLocale("zh-CN");
  assert.throws(() =>
    switchLocale(
      "en",
      () => {
        throw Error("preference save failed");
      },
      () => {},
    ),
  );
  assert.equal(locale(), "zh-CN");
  assert.equal(t("title"), "选择笔记");
  let reloaded = false;
  switchLocale(
    "en",
    () => {},
    () => {
      reloaded = true;
    },
  );
  assert(reloaded);
  assert.equal(lang(), "en");
  assert.equal(t("title"), "Choose notes");
  assert.throws(() =>
    switchLocale(
      "zh",
      () => {},
      () => {
        throw Error("reload failed");
      },
    ),
  );
  assert.equal(locale(), "en");
  assert.equal(lang(), "en");
  setLocale("zh-CN");
});

for (const code of ["storage_busy", "display_busy", "display_changed"])
  test(`${code}: reads retry once; writes and cancelled reads never replay`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const { createAdminApi } =
      await import("../../src/fiber_bundle/web/api.js");
    const { createSession } =
      await import("../../src/fiber_bundle/web/session.js");
    let calls = 0;
    const api = createAdminApi(
      createSession(),
      {
        onConnection: () => {},
        onUnauthorized: () => {},
      },
      async () => {
        calls++;
        return {
          ok: false,
          status: 503,
          json: async () => ({ error: code }),
        };
      },
    );
    const read = api.vaults();
    for (let i = 0; i < 8; i++) await Promise.resolve();
    t.mock.timers.tick(1000);
    await assert.rejects(read, (e) => {
      assert.equal(e.code, code);
      if (code === "display_changed") {
        assert.match(e.message, /读取期间相关数据发生变化/);
        assert(!e.message.includes("后台正在处理其他任务"));
      }
      return true;
    });
    assert.equal(calls, 2);
    calls = 0;
    await assert.rejects(
      api.createClient("synthetic", false),
      (e) => e.code === code,
    );
    assert.equal(calls, 1);
    calls = 0;
    const cancelled = new AbortController();
    const health = api.health({ signal: cancelled.signal });
    for (let i = 0; i < 8; i++) await Promise.resolve();
    cancelled.abort();
    await assert.rejects(health, (e) => e.code === "cancelled");
    assert.equal(calls, 1);
  });
