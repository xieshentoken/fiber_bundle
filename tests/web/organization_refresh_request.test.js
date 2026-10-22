import test from "node:test";
import assert from "node:assert/strict";
import { scopedRead } from "../../src/fiber_bundle/web/organization_refresh_request.js";
test("same-scope reads coalesce while a changed scope stays independent", async () => {
  const waits = new Map();
  const calls = [];
  const read = scopedRead(({ key }) => {
    calls.push(key);
    return new Promise((resolve) => waits.set(key, resolve));
  });
  const first = read({ key: "one" });
  assert.equal(first, read({ key: "one" }));
  const second = read({ key: "two" });
  await Promise.resolve();
  assert.deepEqual(calls, ["one", "two"]);
  waits.get("one")("old");
  await first;
  assert.equal(second, read({ key: "two" }));
  waits.get("two")("new");
  assert.equal(await second, "new");
});
test("a failed read releases its scope so a retry can recover", async () => {
  let count = 0;
  const read = scopedRead(async () => {
    if (++count === 1) throw Error("busy");
    return "ready";
  });
  await assert.rejects(read({ key: "one" }), /busy/);
  assert.equal(await read({ key: "one" }), "ready");
});
