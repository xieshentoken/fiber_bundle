import test from "node:test";
import assert from "node:assert/strict";
import { createAdminApi } from "../../src/fiber_bundle/web/api.js";
import { createSession } from "../../src/fiber_bundle/web/session.js";

test("query operations carry same-origin CSRF and explicit backup approval", async () => {
  const calls = [],
    session = createSession();
  session.accept({ csrf: "synthetic-csrf", expires_at: 99999999999 });
  const api = createAdminApi(
    session,
    { onConnection: () => {}, onUnauthorized: () => {} },
    async (...args) => {
      calls.push(args);
      return { ok: true, json: async () => ({ state: "selected" }) };
    },
  );
  await api.selectQueryBackup("run/one", {
    client_id: "owner",
    backup_index: 1,
    selection_id: "one",
    approve: true,
  });
  assert.equal(calls[0][0], "/admin/query/tasks/cloud/run%2Fone/backup");
  assert.equal(calls[0][1].credentials, "same-origin");
  assert.equal(calls[0][1].headers["X-CSRF-Token"], "synthetic-csrf");
  assert.deepEqual(JSON.parse(calls[0][1].body), {
    client_id: "owner",
    backup_index: 1,
    selection_id: "one",
    approve: true,
  });
  await api.launchQueryJob({
    client_id: "owner",
    role: "local",
    task_id: "task",
    cloud_edit: false,
  });
  await api.cancelQueryJob("job/one");
  await api.recoverQueryJob("job/one");
  await api.queryDetail("cloud", "run/one");
  assert.deepEqual(
    calls.slice(1).map((c) => c[0]),
    [
      "/admin/query/jobs",
      "/admin/query/jobs/job%2Fone/cancel",
      "/admin/query/jobs/job%2Fone/recover",
      "/admin/query/tasks/cloud/run%2Fone",
    ],
  );
});
