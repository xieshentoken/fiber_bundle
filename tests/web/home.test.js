import test from "node:test";
import assert from "node:assert/strict";
import { createHomeData } from "../../src/fiber_bundle/web/home_data.js";
import {
  readState,
  channelState,
  issues,
  pendingItems,
  timeText,
} from "../../src/fiber_bundle/web/home_status.js";
import { createHomeApi } from "../../src/fiber_bundle/web/home_api.js";
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
test("scoped video confirmation and failures appear without duplicating published tasks", async () => {
  const rows = pendingItems({
    videos: {
      value: [
        {
          id: "video-1",
          state: "needs_confirmation",
          client_id: "c",
          vault_id: "v",
        },
        {
          id: "video-2",
          state: "waiting_budget",
          client_id: "c",
          vault_id: "v",
        },
        { id: "video-3", state: "published", client_id: "c", vault_id: "v" },
      ],
    },
  });
  assert.deepEqual(
    rows.map((r) => [r.id, r.page, r.client_id, r.vault_id]),
    [
      ["video-1", "media", "c", "v"],
      ["video-2", "media", "c", "v"],
    ],
  );
  const calls = [];
  const read = createHomeApi((...args) => {
    calls.push(args);
    return Promise.resolve([]);
  });
  assert.throws(() => read("videos"));
  await read("videos", { client_id: "c", vault_id: "v" });
  assert.equal(calls[0][0], "/admin/video-processing?client_id=c&vault_id=v");
  assert.equal(calls[0][1], "GET");
});
test("home de-duplicates identical scoped reads and executes one GET at a time", async () => {
  let active = 0,
    max = 0,
    count = 0;
  const releases = [];
  const data = createHomeData(async (name) => {
    count++;
    max = Math.max(max, ++active);
    await new Promise((r) => releases.push(r));
    active--;
    return { name };
  });
  data.setScope("session:one");
  const first = data.get("a", { vault: "one" }),
    same = data.get("a", { vault: "one" }),
    second = data.get("b");
  assert.equal(first, same);
  assert.equal(count, 1);
  releases.shift()();
  await tick();
  assert.equal(count, 2);
  releases.shift()();
  await Promise.all([first, second]);
  assert.equal(max, 1);
  await data.get("a", { vault: "one" });
  assert.equal(count, 2);
});
test("scope replacement and cancel prevent stale responses, including same-scope re-entry", async () => {
  let release;
  const data = createHomeData(async (name) => {
    if (name === "slow") await new Promise((r) => (release = r));
    return name;
  });
  data.setScope("old");
  const old = data.get("slow");
  const queued = data.get("old-queued");
  data.setScope("new");
  const fresh = data.get("fresh");
  release();
  assert.equal(await old, null);
  assert.equal(await queued, null);
  assert.equal((await fresh).value, "fresh");
  const cached = await data.get("fresh");
  data.cancel();
  assert.equal((await data.get("fresh")).value, cached.value);
});
test("failed refresh keeps last successful value and time without green availability", async () => {
  let now = 10,
    fail = false;
  const data = createHomeData(
    async () => {
      if (fail)
        throw Object.assign(new Error("safe"), {
          status: 503,
          code: "storage_unavailable",
        });
      return 7;
    },
    () => now,
  );
  const first = await data.get("a");
  fail = true;
  now = 80;
  const stale = await data.get("a", {}, { force: true });
  assert.equal(stale.value, 7);
  assert.equal(stale.lastSuccess, first.lastSuccess);
  assert.equal(readState(stale).state, "unverified");
});
test("root storage errors de-duplicate while paused channels are not failures", () => {
  const error = { status: 503, code: "storage_unavailable" };
  assert.equal(issues({ vaults: { error }, backend: { error } }).length, 1);
  assert.equal(channelState({ state: "paused" }).state, "configured");
  assert.equal(
    channelState({
      state: "running",
      binding_state: "bound",
      runtime: { heartbeat_fresh: false },
    }).state,
    "unverified",
  );
  assert.equal(readState().state, "unrecorded");
  assert.equal(timeText(null), "未记录");
});
test("published imports and buffering batches are not personal review tasks", () => {
  assert.equal(
    pendingItems({
      imports: {
        value: [
          { id: "a", state: "published" },
          { id: "b", state: "needs_review" },
          { id: "b", state: "needs_review" },
        ],
      },
      batches: {
        value: [{ id: "x", state: "buffering", needs_question: true }],
      },
    }).length,
    1,
  );
});
test("home contracts reject arbitrary endpoints and unscoped imports", async () => {
  const calls = [];
  const read = createHomeApi(async (...args) => {
    calls.push(args);
    return [];
  });
  assert.throws(() => read("secret"));
  assert.throws(() => read("imports"));
  assert.throws(() => read("vaults", { path: "/private" }));
  await read("imports", { client_id: "client/one", vault_id: "vault?two" });
  assert.equal(calls[0][1], "GET");
  assert.match(calls[0][0], /client%2Fone/);
  assert.equal(calls.length, 1);
});

test("success candidates need publication evidence and automatic work is not a personal todo", () => {
  const rows = [
    { run_id: "unproven", state: "extracted" },
    { run_id: "published", state: "extracted", publication_state: "published" },
    {
      run_id: "owned",
      state: "extracted",
      publication_state: null,
      automatic_state: "queued",
    },
    {
      run_id: "review",
      state: "extracted",
      publication_state: null,
      run_state: "succeeded",
    },
  ];
  const result = pendingItems({
    documents: { value: { tasks: rows } },
    imports: {
      value: [
        {
          id: "owned-import",
          state: "needs_review",
          automatic: { state: "queued" },
        },
      ],
    },
  });
  assert.deepEqual(
    result.map((r) => r.id),
    ["review"],
  );
  assert.equal(
    issues({
      channels: {
        value: {
          channels: [
            {
              id: "c",
              name: "Test",
              state: "running",
              binding_state: "bound",
              runtime: { heartbeat_fresh: false },
            },
          ],
        },
      },
    }).length,
    1,
  );
});
