import test from "node:test";
import assert from "node:assert/strict";
import { installDOM } from "./reliability_dom_fixture.js";
globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { appendContinuity } =
  await import("../../src/fiber_bundle/web/organization_continuity.js");
const { createOrganizationView } =
  await import("../../src/fiber_bundle/web/organization.js");
const proof = {
  plan_sha256: "a".repeat(64),
  completed: 14,
  archived: 9,
  remaining: 1,
};
const fixed = { client_id: "client", vault_id: "vault" };
const job = () => ({
  id: "original",
  maintenance: true,
  mode: "quick",
  state: "waiting_file",
  stage: "applying",
  confirmed: true,
  completed: 14,
  new_mocs: [],
  index_state: "pending",
  index_observation: { state: "building", generation: 15, observed_at: 100 },
  items: Array.from({ length: 15 }, (_, i) => ({
    entity: `note-${i}`,
    title: `Note ${i}`,
    path: `Inbox/${i}.md`,
    evaluated: true,
    status: i > 9 ? "review" : "linked",
    phase: i < 9 ? "archived" : "members_done",
    state: i === 9 ? "waiting_archive" : "done",
    add: [],
    remove: [],
    before: [],
    after: [],
    ...(i === 9 ? { archive_wait: { state: "blocked", links: [] } } : {}),
  })),
});
const button = (host, label) =>
  host.querySelectorAll("button").find((node) => node.textContent === label);
function mount() {
  const dom = installDOM(),
    host = dom.document.createElement("section"),
    notice = dom.document.createElement("p");
  notice.id = "notice";
  dom.document.body.append(notice, host);
  return { ...dom, host };
}

test("normal polling for nine seconds preserves the same close proof, checkbox and in-flight confirmation", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const dom = mount();
  const graphTab = dom.document.createElement("button");
  graphTab.dataset.tab = "graph";
  graphTab.setAttribute("aria-current", "page");
  dom.document.body.append(graphTab);
  let current = job(),
    observed = 100,
    finishResolve;
  const calls = [];
  const result = () => ({
    jobs: [current],
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
  });
  const api = {
    list: async () => result(),
    progress: async () => ({
      jobs: [
        {
          ...structuredClone(current),
          index_observation: {
            ...current.index_observation,
            observed_at: ++observed,
          },
        },
      ],
    }),
    maintenanceAction: async (id, operation, body) => {
      calls.push({ id, operation, body });
      if (operation === "finish_preview")
        return {
          id,
          client_id: fixed.client_id,
          vault_id: fixed.vault_id,
          close_preview: proof,
        };
      return new Promise((resolve) => {
        finishResolve = resolve;
      });
    },
  };
  const view = createOrganizationView(api, () => fixed);
  dom.host.append(view.host);
  try {
    await view.refresh();
    await button(view.host, "结束任务并保留已完成结果…").listeners.click();
    const finish = button(view.host, "确认结束并保留结果"),
      checkbox = finish.parentElement
        .querySelectorAll("input")
        .find((node) => node.type === "checkbox");
    checkbox.checked = true;
    checkbox.listeners.change();
    let previousCard = finish.parentElement.parentElement;
    for (let i = 0; i < 6; i++) {
      t.mock.timers.tick(1500);
      for (let turn = 0; turn < 20; turn++) await Promise.resolve();
      assert.equal(button(view.host, "确认结束并保留结果"), finish);
      assert.equal(checkbox.checked, true);
      assert.equal(observed, 101 + i);
      assert.notEqual(finish.parentElement.parentElement, previousCard);
      previousCard = finish.parentElement.parentElement;
    }
    assert.match(
      finish.parentElement.textContent,
      /保留已处理 14 项及已归档 9 项/,
    );
    const pending = finish.listeners.click();
    assert.equal(finish.disabled, true);
    await finish.listeners.click();
    assert.equal(finish.disabled, true);
    assert.equal(
      calls.filter((value) => value.operation === "finish").length,
      1,
    );
    assert.deepEqual(calls.at(-1), {
      id: "original",
      operation: "finish",
      body: { ...fixed, plan_sha256: proof.plan_sha256 },
    });
    current = { ...current, state: "closed", index_state: "ready" };
    finishResolve();
    await pending;
  } finally {
    view.clear();
    dom.restore();
  }
});

test("changed task facts or index generation invalidate confirmation while pure index observations do not", async () => {
  const dom = mount(),
    panels = new Map(),
    initial = job();
  const api = {
    maintenanceAction: async (id) => ({ id, close_preview: proof }),
  };
  try {
    appendContinuity(dom.host, initial, fixed, api, () => {}, panels);
    await button(dom.host, "结束任务并保留已完成结果…").listeners.click();
    const retained = button(dom.host, "确认结束并保留结果");
    dom.host.replaceChildren();
    appendContinuity(
      dom.host,
      {
        ...initial,
        index_state: "ready",
        index_observation: {
          ...initial.index_observation,
          state: "ready",
          observed_at: 999,
          indexed_generation: 15,
        },
      },
      fixed,
      api,
      () => {},
      panels,
    );
    assert.equal(button(dom.host, "确认结束并保留结果"), retained);
    for (const change of [
      { completed: 13 },
      { state: "paused" },
      { plan_sha256: "new-plan" },
      { items: [] },
      {
        items: initial.items.map((item, index) =>
          index === 9 ? { ...item, archive_operation: "new-operation" } : item,
        ),
      },
      { index_observation: { ...initial.index_observation, generation: 16 } },
    ]) {
      // Each fact is checked against a fresh valid confirmation, independently.
      dom.host.replaceChildren();
      panels.clear();
      appendContinuity(dom.host, initial, fixed, api, () => {}, panels);
      await button(dom.host, "结束任务并保留已完成结果…").listeners.click();
      assert(button(dom.host, "确认结束并保留结果"));
      dom.host.replaceChildren();
      appendContinuity(
        dom.host,
        { ...initial, ...change },
        fixed,
        api,
        () => {},
        panels,
      );
      assert.equal(button(dom.host, "确认结束并保留结果"), undefined);
      assert(button(dom.host, "结束任务并保留已完成结果…"));
    }
  } finally {
    dom.restore();
  }
});

test("a preview returning after owner or fact changes cannot populate another task", async () => {
  const dom = mount(),
    panels = new Map();
  let resolve;
  const api = {
    maintenanceAction: () =>
      new Promise((done) => {
        resolve = done;
      }),
  };
  try {
    appendContinuity(dom.host, job(), fixed, api, () => {}, panels);
    const pending = button(
      dom.host,
      "结束任务并保留已完成结果…",
    ).listeners.click();
    dom.host.replaceChildren();
    appendContinuity(
      dom.host,
      { ...job(), completed: 13 },
      fixed,
      api,
      () => {},
      panels,
    );
    resolve({ id: "original", close_preview: proof });
    await pending;
    assert.equal(button(dom.host, "确认结束并保留结果"), undefined);
    appendContinuity(
      dom.host,
      { ...job(), id: "another" },
      { ...fixed, vault_id: "other" },
      api,
      () => {},
      panels,
    );
    assert.equal(button(dom.host, "确认结束并保留结果"), undefined);
  } finally {
    dom.restore();
  }
});

test("an expired proof cannot submit a close request", async () => {
  const dom = mount(),
    calls = [];
  const api = {
    maintenanceAction: async (id, operation) => {
      calls.push(operation);
      return { id, close_preview: { ...proof, expires_at: 1 } };
    },
  };
  try {
    appendContinuity(dom.host, job(), fixed, api, () => {});
    await button(dom.host, "结束任务并保留已完成结果…").listeners.click();
    const checkbox = dom.host
      .querySelectorAll("input")
      .find((node) => node.type === "checkbox");
    checkbox.checked = true;
    checkbox.listeners.change();
    await button(dom.host, "确认结束并保留结果").listeners.click();
    assert.deepEqual(calls, ["finish_preview"]);
    assert(button(dom.host, "结束任务并保留已完成结果…"));
  } finally {
    dom.restore();
  }
});

test("a finish response returning after changing owner does not refresh another task", async () => {
  const dom = mount(),
    panels = new Map();
  let resolve,
    refreshes = 0;
  const api = {
    maintenanceAction: async (id, operation) =>
      operation === "finish_preview"
        ? { id, close_preview: proof }
        : new Promise((done) => {
            resolve = done;
          }),
  };
  try {
    appendContinuity(dom.host, job(), fixed, api, () => refreshes++, panels);
    await button(dom.host, "结束任务并保留已完成结果…").listeners.click();
    const checkbox = dom.host
      .querySelectorAll("input")
      .find((node) => node.type === "checkbox");
    checkbox.checked = true;
    checkbox.listeners.change();
    const pending = button(dom.host, "确认结束并保留结果").listeners.click();
    dom.host.replaceChildren();
    appendContinuity(
      dom.host,
      { ...job(), id: "another" },
      { ...fixed, vault_id: "other" },
      api,
      () => refreshes++,
      panels,
    );
    resolve();
    await pending;
    assert.equal(refreshes, 0);
    assert.equal(button(dom.host, "确认结束并保留结果"), undefined);
    assert(button(dom.host, "结束任务并保留已完成结果…"));
  } finally {
    dom.restore();
  }
});
