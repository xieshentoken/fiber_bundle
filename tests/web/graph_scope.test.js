import test from "node:test";
import assert from "node:assert/strict";
import {
  GRAPH_SCOPE_KEY,
  createGraphScopePreferences,
  resolveGraphScope,
} from "../../src/fiber_bundle/web/graph_scope_preferences.js";
import { createGraphScopeReads } from "../../src/fiber_bundle/web/graph_scope_read.js";
import { installDOM } from "./reliability_dom_fixture.js";
const A = { client_id: "c", vault_id: "a" },
  B = { client_id: "c", vault_id: "b" };
const clients = [
  { id: "empty", name: "No grants", active: true, grants: [] },
  {
    id: "c",
    name: "Owner",
    active: true,
    grants: [
      { vault_id: "a", can_query: true },
      { vault_id: "b", can_query: true },
    ],
  },
];
const vaults = [
  { id: "a", name: "Alpha", status: "active" },
  { id: "b", name: "Beta", status: "active" },
];
function storage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

test("versioned page defaults save only authorized IDs and clear without business changes", () => {
  const local = storage(),
    prefs = createGraphScopePreferences(local);
  assert.deepEqual(prefs.read(), { scope: null, problem: null });
  assert.equal(prefs.save(B, clients, vaults).ok, true);
  assert.deepEqual(JSON.parse(local.getItem(GRAPH_SCOPE_KEY)), {
    version: 1,
    ...B,
  });
  assert.deepEqual(createGraphScopePreferences(local).read().scope, B);
  assert.equal(
    prefs.save({ ...A, vault_id: "forbidden" }, clients, vaults).ok,
    false,
  );
  assert.deepEqual(prefs.read().scope, B);
  assert.equal(prefs.clear().ok, true);
  assert.equal(prefs.read().scope, null);
});

test("entry applies a saved default while page refresh keeps the temporary selection; invalid defaults do not change storage", () => {
  const local = storage(),
    prefs = createGraphScopePreferences(local);
  prefs.save(B, clients, vaults);
  const resolve = (options) =>
    resolveGraphScope(clients, vaults, {
      saved: prefs.read().scope,
      ...options,
    });
  assert.deepEqual(resolve({ current: A, entering: true }).scope, B);
  assert.deepEqual(resolve({ current: A, entering: false }).scope, A);
  assert.deepEqual(
    resolveGraphScope(clients, vaults, { entering: true }).scope,
    A,
  );
  const revoked = [
    { ...clients[1], grants: [{ vault_id: "a", can_query: true }] },
  ];
  assert.deepEqual(
    resolveGraphScope(revoked, vaults, { entering: true, saved: B }),
    { scope: A, notice: "invalid_default" },
  );
  assert.deepEqual(prefs.read().scope, B);
  assert.deepEqual(
    resolveGraphScope([{ ...clients[1], active: false }], vaults, {
      entering: true,
      saved: B,
    }),
    { scope: null, notice: "no_scope" },
  );
});

test("corrupt JSON, invalid schema, blocked storage, and quota errors remain distinct and never claim persistence", () => {
  for (const value of [
    "broken {",
    "null",
    JSON.stringify({ version: 2, ...A }),
    JSON.stringify({ version: 1, client_id: "bad/path", vault_id: "a" }),
  ]) {
    const local = storage();
    local.setItem(GRAPH_SCOPE_KEY, value);
    assert.equal(
      createGraphScopePreferences(local).read().problem,
      "storage_corrupt",
    );
    assert.equal(local.getItem(GRAPH_SCOPE_KEY), value);
  }
  const blocked = createGraphScopePreferences({
    getItem() {
      throw Error("blocked");
    },
    setItem() {
      throw Error("quota");
    },
    removeItem() {
      throw Error("blocked");
    },
  });
  assert.equal(blocked.read().problem, "storage_unavailable");
  assert.deepEqual(blocked.save(A, clients, vaults), {
    ok: false,
    problem: "save_failed",
  });
  assert.deepEqual(blocked.clear(), { ok: false, problem: "clear_failed" });
  assert.deepEqual(
    resolveGraphScope(clients, vaults, { entering: true }).scope,
    A,
  );
});

test("current duplicate reads coalesce but A→B→A owns a fresh generation and suppresses stale error/finally", async () => {
  const events = [],
    reads = createGraphScopeReads((event) =>
      events.push(event.state + ":" + event.key),
    );
  const firstA = deferred(),
    b = deferred(),
    lastA = deferred();
  let calls = 0;
  const old = reads.run("A", async ({ current }) => {
    calls++;
    await firstA.promise;
    assert.equal(current(), false);
  });
  assert.equal(
    reads.run("A", () => {
      throw Error("duplicate");
    }),
    old,
  );
  await Promise.resolve();
  const second = reads.run("B", () => b.promise);
  await Promise.resolve();
  const latest = reads.run("A", () => {
    calls++;
    return lastA.promise;
  });
  await Promise.resolve();
  assert.notEqual(old, latest);
  firstA.reject(Error("late A error"));
  b.reject(Error("late B error"));
  await old;
  await second;
  assert.deepEqual(events, ["loading:A", "loading:B", "loading:A"]);
  assert.equal(reads.pending("A"), latest);
  lastA.resolve("latest A");
  assert.equal(await latest, "latest A");
  assert.deepEqual(events, ["loading:A", "loading:B", "loading:A", "ready:A"]);
  assert.equal(calls, 2);
  assert.equal(reads.pending("A"), null);
});

test("leaving a page invalidates late success and failure, while current failures stay actionable", async () => {
  const events = [],
    reads = createGraphScopeReads((event) => events.push(event.state));
  const late = deferred();
  const pending = reads.run("A", () => late.promise);
  await Promise.resolve();
  reads.cancel();
  late.reject(Error("after logout"));
  await pending;
  assert.deepEqual(events, ["loading", "idle"]);
  await assert.rejects(
    reads.run("B", () => Promise.reject(Error("current failure"))),
    /current failure/,
  );
  assert.deepEqual(events.slice(-2), ["loading", "failed"]);
});

test("shared default controls save and clear without new reads; failed storage is bilingual and preserves selection", async () => {
  globalThis.localStorage = storage();
  const { createGraphScopeControls } =
    await import("../../src/fiber_bundle/web/graph_scope_controls.js");
  const { setLocale } =
    await import("../../src/fiber_bundle/web/i18n/index.js");
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const local = storage();
      let current = B;
      const controls = createGraphScopeControls(() => current, {
        preferences: createGraphScopePreferences(local),
      });
      assert.deepEqual(controls.catalog(clients, vaults, null, true), A);
      controls.changed();
      controls.host
        .querySelector('[data-scope-default="save"]')
        .listeners.click();
      assert.deepEqual(JSON.parse(local.getItem(GRAPH_SCOPE_KEY)), {
        version: 1,
        ...B,
      });
      assert.match(controls.host.textContent, /Owner \/ Beta/);
      current = A;
      controls.changed();
      assert.match(controls.host.textContent, /Owner \/ Beta/);
      controls.host
        .querySelector('[data-scope-default="clear"]')
        .listeners.click();
      assert.equal(local.getItem(GRAPH_SCOPE_KEY), null);
      assert.deepEqual(current, A);
      const unavailable = createGraphScopeControls(() => current, {
        preferences: createGraphScopePreferences({
          getItem: () => null,
          setItem() {
            throw Error("quota");
          },
          removeItem() {
            throw Error("blocked");
          },
        }),
      });
      unavailable.catalog(clients, vaults, null, true);
      unavailable.host
        .querySelector('[data-scope-default="save"]')
        .listeners.click();
      assert.match(
        unavailable.host.textContent,
        language === "en" ? /could not be saved/ : /保存失败/,
      );
      assert.doesNotMatch(
        unavailable.host.textContent,
        language === "en" ? /Page default saved\./ : /页面默认已保存/,
      );
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("pending scope consent rechecks authorization, submissions, current scope, and lifecycle before applying", async () => {
  const { createGraphScopeSwitch } =
    await import("../../src/fiber_bundle/web/graph_scope_switch.js");
  for (const change of ["authorization", "submission", "scope", "lifecycle"]) {
    const answer = deferred();
    let current = A,
      busy = false,
      allowed = true,
      applied = 0;
    const switching = createGraphScopeSwitch({
      current: () => current,
      restore: (value) => {
        current = value;
      },
      apply: () => {
        applied++;
      },
      dirty: () => true,
      busy: () => busy,
      authorized: () => allowed,
      confirm: () => answer.promise,
      name: (value) => value.vault_id,
    });
    const pending = switching.request(B);
    assert.equal(current.vault_id, "a");
    assert.equal(switching.request(B), pending);
    if (change === "authorization") allowed = false;
    if (change === "submission") busy = true;
    if (change === "scope") current = { client_id: "other", vault_id: "other" };
    if (change === "lifecycle") switching.cancel();
    answer.resolve(true);
    await pending;
    assert.equal(applied, 0, change);
  }
});

test("an uncommitted scope can recover without drafts, and confirmation exceptions are reported only at their own exit", async () => {
  const { createGraphScopeSwitch } =
    await import("../../src/fiber_bundle/web/graph_scope_switch.js");
  let current = null,
    edited = false,
    displayed = A,
    count = 0;
  const errors = [];
  const switching = createGraphScopeSwitch({
    current: () => current,
    restore: (value) => {
      displayed = value;
    },
    apply: (value) => {
      displayed = value;
      count++;
    },
    dirty: () => edited,
    busy: () => false,
    authorized: () => true,
    confirm: async () => {
      throw Error("dialog failed");
    },
    name: (value) => value.vault_id,
    onError: (error) => errors.push(error.message),
  });
  switching.remember(A);
  await switching.request(B);
  assert.equal(count, 1);
  assert.equal(displayed.vault_id, "b");
  edited = true;
  await switching.request(A);
  assert.equal(count, 1);
  assert.match(errors[0], /尚未核对/);
  current = B;
  await switching.request(A);
  assert.equal(count, 1);
  assert.match(errors[1], /无法完成切换确认/);
  assert.equal(errors.length, 2);
});
