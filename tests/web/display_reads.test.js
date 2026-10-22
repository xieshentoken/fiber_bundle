import test from "node:test";
import assert from "node:assert/strict";
import { createStore } from "../../src/fiber_bundle/web/atlas/store.js";

test("concurrent forced refresh joins the active read; explicit invalidation fetches current bindings", async () => {
  let calls = 0,
    finish;
  const store = createStore({
    vaults: () => {
      calls++;
      return new Promise((resolve) => (finish = resolve));
    },
  });
  const first = store.vaults({ force: true });
  const second = store.vaults({ force: true });
  assert.equal(calls, 1);
  finish([{ id: "one", status: "active", attachment_state: "attached" }]);
  assert.deepEqual(await first, await second);
  assert.equal((await store.vaults())[0].id, "one");
  assert.equal(calls, 1);
  store.invalidate("vaults");
  const current = store.vaults();
  assert.equal(calls, 2);
  finish([{ id: "two", status: "active", attachment_state: "attached" }]);
  assert.equal((await current)[0].id, "two");
});
