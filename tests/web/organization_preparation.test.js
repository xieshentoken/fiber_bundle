import test from "node:test";
import assert from "node:assert/strict";
import { prepareWaitingSources } from "../../src/fiber_bundle/web/organization_preparation.js";
globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { itemStages, stageTotals } =
  await import("../../src/fiber_bundle/web/organization_stages.js");
import {
  maintenancePhase,
  itemCategory,
} from "../../src/fiber_bundle/web/organization_progress.js";
import { messages } from "../../src/fiber_bundle/web/i18n/catalog.js";
const { phrase, setLocale } =
  await import("../../src/fiber_bundle/web/i18n/index.js");

test("explicit preparation bounds requests, retains partial progress, and resumes once ready", async () => {
  const job = {
    id: "task",
    waiting_sources: Array.from({ length: 12 }, (_, i) => `Inbox/${i}.md`),
  };
  const calls = [],
    actions = [];
  const api = {
    cloudFile: async (value) => {
      calls.push(value);
      return { state: "available", expected: "token" };
    },
    maintenanceAction: async (...args) => actions.push(args),
  };
  const first = await prepareWaitingSources(job, { vault_id: "vault" }, api);
  assert.equal(first.ready, 10);
  assert.equal(first.resumed, false);
  assert.equal(calls.length, 20);
  assert.deepEqual(actions, []);
  const second = await prepareWaitingSources(job, {}, api, {
    previous: first.results,
    offset: first.nextOffset,
  });
  assert.equal(second.ready, 12);
  assert.equal(second.resumed, true);
  assert.equal(actions.length, 1);
  assert.equal(actions[0][1], "resume");
  assert(calls.every((c) => !c.action || c.action === "recheck"));
});

test("unknown download and a detached view cannot cause repeated downloads or resume", async () => {
  let active = true;
  const calls = [],
    actions = [];
  const api = {
    cloudFile: async (v) => {
      calls.push(v);
      if (v.action === "download") {
        active = false;
        return { state: "unknown", expected: "token" };
      }
      return { state: "not_requested", expected: "token" };
    },
    maintenanceAction: async (...a) => actions.push(a),
  };
  const result = await prepareWaitingSources(
    { id: "x", waiting_sources: ["a.md", "b.md"] },
    {},
    api,
    { active: () => active },
  );
  assert.equal(result.results.length, 1);
  assert.deepEqual(
    calls.map((c) => c.action || "inspect"),
    ["inspect", "download"],
  );
  assert.deepEqual(actions, []);
});

test("pending, conflicting and unknown system states are inspected without downloading again", async () => {
  for (const state of ["requested", "downloading", "conflict", "unknown"]) {
    const calls = [];
    const api = {
      cloudFile: async (v) => {
        calls.push(v);
        return { state };
      },
      maintenanceAction: () => assert.fail("no resume"),
    };
    await prepareWaitingSources({ waiting_sources: ["a.md"] }, {}, api);
    assert.equal(calls.length, 1);
  }
});

test("mounted but unarchived work remains visible independently of task completion", () => {
  const item = {
    evaluated: true,
    state: "waiting_archive",
    phase: "members_done",
    after: ["moc"],
    membership_observation: { state: "current", moc_ids: ["moc"] },
    deferred_target: "Resource/a.md",
  };
  const job = { mode: "quick", state: "waiting_file", items: [item] };
  assert.deepEqual(itemStages(item, job), {
    classification: "已分析",
    membership: "已挂载",
    archive: "归档延后",
  });
  assert.deepEqual(stageTotals(job), {
    analyzed: 1,
    mounted: 1,
    archived: 0,
    waiting: 1,
  });
  assert.equal(itemCategory(item, job), "waiting");
  assert.match(maintenancePhase(job), /等待文件/);
});

test("new continuity copy and parameterized status have English translations", () => {
  const source = "已分析 {p0} · 当前已挂载 {p1} · 已归档 {p2} · 等待 {p3}";
  assert(Object.values(messages).some((pair) => pair[0] === source));
  setLocale("en");
  assert.equal(
    String(phrase(source, { p0: 2, p1: 1, p2: 0, p3: 1 })),
    "Analyzed 2 · Currently mounted 1 · Archived 0 · Waiting 1",
  );
  setLocale("zh-CN");
});


test("legacy phases and stale receipts cannot report a current mounting", () => {
  for (const extra of [
    {},
    { membership_observation: { state: "missing", moc_ids: [] } },
    { membership_observation: { state: "unavailable", moc_ids: [] } },
    { manual_resolution: { state: "resolved", method: "manual_moc", moc_ids: ["moc"] } },
  ]) {
    const item = { evaluated: true, state: "done", phase: "archived", after: ["moc"], ...extra };
    const job = { mode: "quick", items: [item] };
    assert.equal(itemStages(item, job).membership, "挂载待核对");
    assert.equal(stageTotals(job).mounted, 0);
    assert.equal(stageTotals(job).archived, 1);
  }
});

test("membership verification and stale-plan recovery errors are bilingual", async () => {
  const { requestErrors } = await import("../../src/fiber_bundle/web/request_errors.js");
  setLocale("en");
  assert.equal(String(phrase("挂载待核对")), "Membership needs verification");
  for (const code of ["maintenance_membership_plan_changed", "maintenance_membership_baseline_changed"]) {
    assert.match(requestErrors[code][0], /保留已完成结果/);
    assert.match(requestErrors[code][1], /keep completed results/);
  }
  setLocale("zh-CN");
});
