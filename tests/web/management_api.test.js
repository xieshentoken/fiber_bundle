import test from "node:test";
import assert from "node:assert/strict";
import { createAdminApi } from "../../src/fiber_bundle/web/api.js";
import { createSession } from "../../src/fiber_bundle/web/session.js";

test("management requests use cookie/CSRF and exact revisions, no inference toggles", async () => {
  const calls = [],
    session = createSession();
  session.accept({ csrf: "synthetic", expires_at: Date.now() / 1000 + 10 });
  const api = createAdminApi(
    session,
    { onConnection() {}, onUnauthorized() {} },
    async (path, options) => {
      calls.push([path, options]);
      return { ok: true, json: async () => ({ state: "prepared" }) };
    },
  );
  await api.stopBackend("instance");
  await api.appendCreation("task/one", {
    expected_revision: 2,
    expected_sha256: "a".repeat(64),
    amendment_id: "next",
    addition: "detail",
  });
  await api.approveCreationSave("task/one", "b".repeat(64));
  assert.deepEqual(
    calls.map((c) => c[0]),
    [
      "/admin/backend/stop",
      "/admin/document-tasks/task%2Fone/append",
      "/admin/document-tasks/task%2Fone/save-approval",
    ],
  );
  for (const [, options] of calls) {
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.headers["X-CSRF-Token"], "synthetic");
    assert.equal(JSON.parse(options.body).allow_inference, undefined);
  }
  assert.equal(JSON.parse(calls[1][1].body).expected_revision, 2);
});
test("late binary response from an expired session is not downloaded", async () => {
  const session = createSession();
  session.accept({ csrf: "synthetic", expires_at: 1 });
  const api = createAdminApi(
    session,
    { onConnection() {}, onUnauthorized() {} },
    async () => ({
      ok: true,
      blob: async () => {
        session.clear();
        return new Blob(["private draft"]);
      },
    }),
  );
  await assert.rejects(api.downloadCreation("one"), /会话已变化/);
});

test("backup API sends bounded intents without passwords or arbitrary paths", async () => {
  const calls = [];
  const value = {
    id: "synthetic-id",
    action: "create",
    state: "prepared",
    destination: "/fresh",
    command: "fiber-backup execute",
    terminal_password_required: true,
    services_started: false,
  };
  const api = createAdminApi(
    createSession(),
    { onConnection() {}, onUnauthorized() {} },
    async (path, options) => {
      calls.push([path, options]);
      return {
        ok: true,
        status: 200,
        json: async () => (options.method === "GET" ? [value] : value),
      };
    },
  );
  assert.deepEqual(await api.backups(), [value]);
  await api.prepareBackup("create");
  assert.equal(calls[1][0], "/admin/backups");
  assert.deepEqual(JSON.parse(calls[1][1].body), { action: "create" });
  await api.prepareBackup("restore", "bundle-id");
  assert.deepEqual(JSON.parse(calls[2][1].body), {
    action: "restore",
    bundle: "bundle-id",
  });
});

test("backup scope blockers use bounded local copy without rendering server details", async () => {
  for (const code of ["backup_unclassified_state", "private-path-sentinel"]) {
    const api = createAdminApi(
      createSession(),
      { onConnection() {}, onUnauthorized() {} },
      async () => ({
        ok: false,
        status: 422,
        json: async () => ({ error: code, detail: "private-content-sentinel" }),
      }),
    );
    await assert.rejects(api.prepareBackup("create"), (error) => {
      assert.equal(error.status, 422);
      assert.equal(error.message.includes("private"), false);
      assert.equal(
        error.message.includes("未分类"),
        code === "backup_unclassified_state",
      );
      return true;
    });
  }
});
