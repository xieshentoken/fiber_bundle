import test from "node:test";
import assert from "node:assert/strict";
import { createSession } from "../../src/fiber_bundle/web/session.js";
import { createAdminApi, ApiError } from "../../src/fiber_bundle/web/api.js";

const ok = (data) => ({ ok: true, json: async () => data });
function fixture(fetcher) {
  const session = createSession(),
    connections = [];
  let unauthorized = 0;
  const api = createAdminApi(
    session,
    {
      onConnection: (value) => connections.push(value),
      onUnauthorized: () => {
        unauthorized++;
        session.clear();
      },
    },
    fetcher,
  );
  return {
    api,
    session,
    connections,
    get unauthorized() {
      return unauthorized;
    },
  };
}
test("manual batches wait beyond ten seconds and return all three results without retry", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let finish,
    signal,
    calls = 0;
  const f = fixture(async (path, options) => {
    assert.equal(path, "/admin/organization/members/batch");
    calls++;
    signal = options.signal;
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  const pending = f.api.organization.members({
    moc_ids: ["a", "b", "c"],
    action: "link",
  });
  t.mock.timers.tick(20_000);
  assert.equal(signal.aborted, false);
  finish(
    ok({
      completed: true,
      results: ["a", "b", "c"].map((moc_id) => ({ moc_id, completed: true })),
    }),
  );
  assert.equal((await pending).results.length, 3);
  assert.equal(calls, 1);
});
test("manual batch timeout remains bounded and never resends the write", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  const f = fixture(async (_, options) => {
    calls++;
    return new Promise((resolve, reject) =>
      options.signal.addEventListener("abort", () =>
        reject(new Error("aborted")),
      ),
    );
  });
  const pending = f.api.organization.members({
    moc_ids: ["a", "b", "c"],
    action: "link",
  });
  t.mock.timers.tick(120_000);
  await assert.rejects(pending, (error) => error.code === "timeout");
  assert.equal(calls, 1);
  assert.deepEqual(f.connections, []);
});
test("session validates before accepting and clears at the expiry boundary", () => {
  const session = createSession();
  assert.throws(() => session.accept({ csrf: "", expires_at: 10 }));
  assert.equal(session.revision, 0);
  session.accept({ csrf: "synthetic-csrf", expires_at: 10 });
  assert.equal(session.expired(9), false);
  assert.equal(session.expired(10), true);
  session.clear();
  assert.equal(session.csrf, "");
  assert.equal(session.expiresAt, 0);
  assert.equal(session.expired(11), false);
});
test("named operations encode path IDs and use same-origin cookie plus CSRF", async () => {
  const calls = [];
  const f = fixture(async (...args) => {
    calls.push(args);
    return ok({ updated: true });
  });
  f.session.accept({ csrf: "synthetic-csrf", expires_at: 100 });
  await f.api.setPermissions("client/one", "vault?two", {
    query: true,
    original: false,
    add: false,
  });
  assert.equal(calls[0][0], "/admin/clients/client%2Fone/vaults/vault%3Ftwo");
  assert.equal(calls[0][1].credentials, "same-origin");
  assert.equal(calls[0][1].headers["X-CSRF-Token"], "synthetic-csrf");
  assert.equal(calls[0][1].method, "PUT");
  assert.deepEqual(JSON.parse(calls[0][1].body), {
    query: true,
    original: false,
    add: false,
  });
});
test("401 clears the current session without rendering any server error body", async () => {
  const f = fixture(async () => ({
    ok: false,
    status: 401,
    json: () => {
      throw Error("must not read");
    },
  }));
  f.session.accept({ csrf: "synthetic-csrf", expires_at: 100 });
  await assert.rejects(
    f.api.clients(),
    (error) => error instanceof ApiError && error.status === 401,
  );
  assert.equal(f.unauthorized, 1);
  assert.equal(f.session.csrf, "");
});
test("network failure reports offline without converting it into sign-out", async () => {
  const f = fixture(async () => {
    throw Error("private diagnostic");
  });
  f.session.accept({ csrf: "synthetic-csrf", expires_at: 100 });
  await assert.rejects(
    f.api.health(),
    (error) => !error.message.includes("private") && error.status === 0,
  );
  assert.deepEqual(f.connections, [false]);
  assert.equal(f.unauthorized, 0);
});

test("Agent revision conflict reports recovery and preserves the safe diagnostic code", async () => {
  const f = fixture(async () => ({
    ok: false,
    status: 409,
    json: async () => ({ error: "agent_revision_conflict" }),
  }));
  await assert.rejects(
    f.api.agents.save("synthetic", {}),
    (error) =>
      error.status === 409 &&
      /编辑已保留.*agent_revision_conflict/.test(error.message),
  );
});
test("late unauthorized response cannot clear a newly established session", async () => {
  let finish;
  const f = fixture(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const pending = f.api.clients();
  f.session.accept({ csrf: "new-synthetic-csrf", expires_at: 200 });
  finish({ ok: false, status: 401 });
  await assert.rejects(pending, (error) => error.status === 409);
  assert.equal(f.session.csrf, "new-synthetic-csrf");
  assert.equal(f.unauthorized, 0);
});
test("late decoded response is discarded after session clear", async () => {
  let finish;
  const f = fixture(async () => ({
    ok: true,
    json: () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  }));
  const pending = f.api.clients();
  await Promise.resolve();
  await Promise.resolve();
  f.session.clear();
  finish([]);
  await assert.rejects(pending, (error) => error.status === 409);
});
test("malformed server collections fail before feature rendering", async () => {
  const f = fixture(async () =>
    ok([{ id: "id", name: "name", grants: "invalid" }]),
  );
  await assert.rejects(f.api.clients(), /后台响应格式无效/);
  await assert.rejects(f.api.vaults(), /后台响应格式无效/);
});
test("validated lists preserve false permissions and pending status", async () => {
  const f = fixture(async (path) =>
    ok(
      path.endsWith("clients")
        ? [
            {
              id: "c",
              name: "synthetic",
              active: 1,
              create_vault: 0,
              grants: [
                { vault_id: "v", can_query: 0, can_original: 0, can_add: 1 },
              ],
            },
          ]
        : [
            {
              id: "v",
              name: "synthetic",
              status: "pending",
              default_query: 0,
              requested_by_client_id: "c",
            },
          ],
    ),
  );
  assert.equal((await f.api.clients())[0].grants[0].can_original, 0);
  assert.equal((await f.api.vaults())[0].status, "pending");
});
test("batch choices use admin session and reject malformed list data", async () => {
  const calls = [];
  const f = fixture(async (path, options) => {
    calls.push([path, options]);
    return ok(
      path === "/admin/batches"
        ? [
            {
              id: "batch/one",
              state: "frozen",
              count: 2,
              received_at: 100,
              preview: "合成正文",
              content_ready: true,
              needs_question: false,
            },
          ]
        : { action: "save_one", dispatched: false },
    );
  });
  f.session.accept({ csrf: "synthetic-csrf", expires_at: 100 });
  assert.equal((await f.api.pendingBatches())[0].count, 2);
  await f.api.chooseBatch("batch/one", "save_one");
  assert.equal(calls[1][0], "/admin/batches/batch%2Fone/choice");
  assert.equal(calls[1][1].headers["X-CSRF-Token"], "synthetic-csrf");
  assert.equal(calls[1][1].method, "POST");
  assert.deepEqual(JSON.parse(calls[1][1].body), { action: "save_one" });
  const malformed = fixture(async () => ok([{ id: "batch", count: -1 }]));
  await assert.rejects(malformed.api.pendingBatches(), /后台响应格式无效/);
});

test("batch deadlines are optional for old servers but otherwise finite positive timestamps", async () => {
  const base = {
    id: "one",
    state: "buffering",
    count: 1,
    received_at: 100,
    preview: "synthetic",
    content_ready: false,
    needs_question: false,
  };
  for (const deadline of [undefined, null, 103]) {
    const f = fixture(async () => ok([{ ...base, freeze_at: deadline }]));
    assert.equal((await f.api.pendingBatches())[0].id, "one");
  }
  for (const deadline of ["soon", -1, 0]) {
    const f = fixture(async () => ok([{ ...base, freeze_at: deadline }]));
    await assert.rejects(f.api.pendingBatches(), /后台响应格式无效/);
  }
});

test("home errors retain only bounded structured codes and cancellable GET does not sign out", async () => {
  const f = fixture(async () => ({
    ok: false,
    status: 503,
    json: async () => ({
      error: "storage_unavailable",
      detail: "/private/secret",
    }),
  }));
  await assert.rejects(
    f.api.homeRead("backend"),
    (e) =>
      e.status === 503 &&
      e.code === "storage_unavailable" &&
      !e.message.includes("private"),
  );
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    f.api.homeRead("backend", {}, controller.signal),
    (e) => e.code === "cancelled",
  );
  assert.equal(f.unauthorized, 0);
});
