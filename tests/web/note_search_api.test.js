import test from "node:test";
import assert from "node:assert/strict";
import { createAdminApi } from "../../src/fiber_bundle/web/api.js";
import { createSession } from "../../src/fiber_bundle/web/session.js";

const found = {
  status: "evidence_found",
  searched_vault_ids: ["vault"],
  results: [
    {
      vault_id: "vault",
      note_path: "note.md",
      title: "Synthetic note",
      snippet: "Synthetic evidence",
      line: 2,
    },
  ],
};
const response = (value) => ({ ok: true, json: async () => value });
function api(fetcher, onMutation = () => {}) {
  const session = createSession();
  session.accept({ csrf: "synthetic-csrf", expires_at: 100 });
  return createAdminApi(
    session,
    { onConnection() {}, onUnauthorized() {}, onMutation },
    fetcher,
  );
}

test("note search uses the admin session without tokens or mutation invalidation", async () => {
  const calls = [];
  let mutations = 0;
  const client = api(
    async (...args) => {
      calls.push(args);
      return response(found);
    },
    () => mutations++,
  );
  assert.deepEqual(await client.searchNotes("owner", "synthetic", 20), found);
  assert.equal(calls.length, 1);
  const [path, options] = calls[0];
  assert.equal(path, "/admin/note-search");
  assert.equal(options.method, "POST");
  assert.equal(options.credentials, "same-origin");
  assert.equal(options.headers["X-CSRF-Token"], "synthetic-csrf");
  assert.equal(options.headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(options.body), {
    client_id: "owner",
    query: "synthetic",
    limit: 20,
  });
  assert.equal(mutations, 0);
});

test("note search accepts empty results and rejects malformed or foreign-vault hits", async () => {
  const empty = { status: "no_evidence", searched_vault_ids: [], results: [] };
  assert.deepEqual(
    await api(async () => response(empty)).searchNotes("owner", "xx"),
    empty,
  );
  for (const invalid of [
    {},
    { ...found, results: [{ ...found.results[0], line: 0 }] },
    { ...found, results: [{ ...found.results[0], snippet: {} }] },
    { ...found, searched_vault_ids: [] },
  ]) {
    await assert.rejects(
      api(async () => response(invalid)).searchNotes("owner", "xx"),
    );
  }
});

test("note search remains bounded at 60 seconds without resubmitting", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0,
    signal;
  const client = api(async (_path, options) => {
    calls++;
    signal = options.signal;
    return new Promise((_resolve, reject) =>
      signal.addEventListener("abort", () => reject(Error("abort"))),
    );
  });
  const pending = client.searchNotes("owner", "xx");
  t.mock.timers.tick(10001);
  assert.equal(signal.aborted, false);
  t.mock.timers.tick(49999);
  await assert.rejects(pending, (error) => error.code === "timeout");
  assert.equal(calls, 1);
});

test("superseded note search cancels and busy failure does not replay POST", async () => {
  const controller = new AbortController();
  let calls = 0;
  const client = api(async (_path, options) => {
    calls++;
    return new Promise((_resolve, reject) =>
      options.signal.addEventListener("abort", () => reject(Error("abort"))),
    );
  });
  const pending = client.searchNotes("owner", "xx", 20, {
    signal: controller.signal,
  });
  controller.abort();
  await assert.rejects(pending, (error) => error.code === "cancelled");
  assert.equal(calls, 1);
  const busy = api(async () => {
    calls++;
    return {
      ok: false,
      status: 503,
      json: async () => ({ error: "storage_busy" }),
    };
  });
  await assert.rejects(
    busy.searchNotes("owner", "xx"),
    (error) => error.code === "storage_busy",
  );
  assert.equal(calls, 2);
});
