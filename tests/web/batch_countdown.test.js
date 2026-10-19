import test from "node:test";
import assert from "node:assert/strict";
import { createBatchCountdown } from "../../src/fiber_bundle/web/batch_countdown.js";

function clock() {
  let time = 100000,
    sequence = 0;
  const tasks = new Map();
  return {
    now: () => time,
    schedule(work, delay) {
      const id = ++sequence;
      tasks.set(id, { work, at: time + delay });
      return id;
    },
    cancel(id) {
      tasks.delete(id);
    },
    async advance(ms) {
      time += ms;
      for (const [id, task] of [...tasks])
        if (task.at <= time) {
          tasks.delete(id);
          await task.work();
        }
    },
  };
}
const batch = (id, deadline) => ({
  id,
  state: "buffering",
  freeze_at: deadline,
});

test("re-rendering a shared deadline coalesces the read without mutating batch state", async () => {
  const c = clock();
  let reads = 0;
  const items = [batch("one", 101), batch("two", 101)];
  const timer = createBatchCountdown(
    async () => {
      reads++;
    },
    assert.fail,
    c,
  );
  timer.setVisible(true);
  for (let i = 0; i < 3; i++) timer.update(items);
  await c.advance(1100);
  assert.equal(reads, 1);
  assert.equal(items[0].state, "buffering");
  timer.update(items);
  await c.advance(2000);
  assert.equal(reads, 1);
});
test("extending a deadline replaces the prior check; leaving cancels it", async () => {
  const c = clock();
  let reads = 0;
  const timer = createBatchCountdown(
    async () => {
      reads++;
    },
    assert.fail,
    c,
  );
  timer.setVisible(true);
  timer.update([batch("one", 101)]);
  timer.update([batch("one", 103)]);
  await c.advance(1100);
  assert.equal(reads, 0);
  timer.setVisible(false);
  await c.advance(2500);
  assert.equal(reads, 0);
  timer.setVisible(true);
  timer.update([batch("one", 105)]);
  timer.clear();
  await c.advance(2000);
  assert.equal(reads, 0);
});
test("different deadlines are read once each without an expiry retry loop", async () => {
  const c = clock();
  let reads = 0;
  const timer = createBatchCountdown(
    async () => {
      reads++;
    },
    assert.fail,
    c,
  );
  timer.update([batch("one", 101), batch("two", 103)]);
  timer.setVisible(true);
  await c.advance(1100);
  assert.equal(reads, 1);
  await c.advance(2000);
  assert.equal(reads, 2);
  await c.advance(10000);
  assert.equal(reads, 2);
});
test("an expiry read failure is visible, and a late failure after leaving is ignored", async () => {
  const c = clock(),
    errors = [];
  let finish;
  const timer = createBatchCountdown(
    () =>
      new Promise((_, reject) => {
        finish = reject;
      }),
    (e) => errors.push(e.message),
    c,
  );
  timer.update([batch("one", 101)]);
  timer.setVisible(true);
  const pending = c.advance(1100);
  finish(new Error("offline"));
  await pending;
  assert.deepEqual(errors, ["offline"]);
  timer.update([batch("two", 103)]);
  const late = c.advance(2000);
  timer.setVisible(false);
  timer.setVisible(true);
  finish(new Error("old failure"));
  await late;
  assert.deepEqual(errors, ["offline"]);
  timer.clear();
});
