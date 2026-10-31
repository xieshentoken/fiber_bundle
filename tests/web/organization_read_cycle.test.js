import test from "node:test";
import assert from "node:assert/strict";
import {
  readOrganizationCycle,
  progressRetryDelay,
} from "../../src/fiber_bundle/web/organization_read_cycle.js";

test("failed catalog reads once; repeated automatic polls read only progress; manual refresh recovers", async () => {
  const calls = [];
  let fail = true;
  const data = { jobs: [{ id: "a", state: "running" }], mocs: [], total: 0 };
  const api = {
    list: async () => {
      calls.push("catalog");
      if (fail) throw { status: 409, code: "organization_moc_unavailable" };
      return data;
    },
    progress: async () => {
      calls.push("progress");
      return { jobs: data.jobs };
    },
  };
  let result = await readOrganizationCycle(api, {}, null);
  assert.equal(result.catalog, false);
  assert.equal(result.error.code, "organization_moc_unavailable");
  assert.deepEqual(calls, ["catalog", "progress"]);
  for (let n = 0; n < 20; n++)
    result = await readOrganizationCycle(api, {}, result.data, {
      automatic: true,
    });
  assert.equal(calls.filter((c) => c === "catalog").length, 1);
  fail = false;
  result = await readOrganizationCycle(api, {}, result.data);
  assert.equal(result.catalog, true);
  assert.equal(result.error, null);
});
test("a completed application triggers only one catalog read even if it fails", async () => {
  let catalogs = 0;
  const api = {
    progress: async () => ({ jobs: [{ id: "a", state: "completed" }] }),
    list: async () => {
      catalogs++;
      throw Error("cloud");
    },
  };
  const result = await readOrganizationCycle(
    api,
    {},
    { jobs: [{ id: "a", state: "running" }] },
    { automatic: true },
  );
  assert.equal(result.catalog, false);
  await readOrganizationCycle(api, {}, result.data, { automatic: true });
  assert.equal(catalogs, 1);
});
test("revoked scope never returns the cached catalog or ledger", async () => {
  let calls = 0;
  await assert.rejects(
    readOrganizationCycle(
      {
        list: async () => {
          throw { status: 403 };
        },
        progress: async () => {
          calls++;
        },
      },
      {},
      { jobs: [] },
    ),
    (e) => e.status === 403,
  );
  assert.equal(calls, 0);
});
test("automatic retry after first load failure never rescans; progress retries are bounded", async () => {
  await readOrganizationCycle(
    {
      list: async () => {
        throw Error("unexpected scan");
      },
      progress: async () => ({ jobs: [] }),
    },
    {},
    null,
    { automatic: true },
  );
  assert.deepEqual([0, 1, 2, 3, 10].map(progressRetryDelay), [
    30000,
    60000,
    120000,
    null,
    null,
  ]);
});

test("current index progress keeps polling after a historical failure and stops when ready", async () => {
  const { organizationWorking } =
    await import("../../src/fiber_bundle/web/organization_read_cycle.js");
  for (const state of ["pending", "building", "waiting"])
    assert.equal(
      organizationWorking({
        jobs: [
          {
            state: "completed",
            index_state: "failed",
            index_observation: { state },
          },
        ],
      }),
      true,
    );
  assert.equal(
    organizationWorking({
      jobs: [
        {
          state: "completed",
          index_state: "failed",
          index_observation: { state: "ready", after_task: true },
        },
      ],
    }),
    false,
  );
  assert.equal(
    organizationWorking({
      jobs: [{ state: "completed", index_state: "failed" }],
    }),
    false,
  );
});

test("a new preview is visible from the ledger without waiting for a catalog scan", async () => {
  const old = { id: "old", state: "completed", items: Array(29).fill({}) };
  const preview = { id: "new", state: "preview", items: Array(4).fill({}) };
  const result = await readOrganizationCycle(
    {
      list: async () => {
        throw Error("a preview must not start a catalog scan");
      },
      progress: async () => ({ jobs: [preview, old] }),
    },
    {},
    { jobs: [old] },
    { automatic: true },
  );
  assert.equal(result.catalog, null);
  assert.equal(result.data.jobs[0].id, "new");
  assert.equal(result.data.jobs[0].items.length, 4);
});
