import test from "node:test";
import assert from "node:assert/strict";
import { createBackupPolling } from "../../src/fiber_bundle/web/backup_polling.js";
const active = { jobs: [{ state: "running" }] };
const done = { jobs: [{ state: "failed" }] };
function harness(read, visible = () => true) {
  let pending = null,
    delay = null;
  const values = [],
    errors = [];
  const poll = createBackupPolling({
    read,
    visible,
    onValue: (value) => values.push(value),
    onError: (error) => errors.push(error),
    schedule: (fn, ms) => {
      pending = fn;
      delay = ms;
      return 1;
    },
    cancel: () => {
      pending = null;
    },
  });
  return {
    poll,
    values,
    errors,
    tick: () => {
      const work = pending;
      pending = null;
      return work?.();
    },
    get delay() {
      return delay;
    },
    get pending() {
      return pending;
    },
  };
}
test("backup polling refreshes active status then slows after failure without submitting", async () => {
  let calls = 0;
  const h = harness(async () => {
    calls++;
    return done;
  });
  h.poll.start(active);
  assert.equal(h.delay, 2000);
  await h.tick();
  assert.equal(calls, 1);
  assert.deepEqual(h.values, [done]);
  assert.equal(h.delay, 10000);
  h.poll.stop();
  assert.equal(h.pending, null);
});
test("backup polling discards late reads and skips hidden pages", async () => {
  let finish,
    calls = 0;
  const h = harness(() => {
    calls++;
    return new Promise((resolve) => {
      finish = resolve;
    });
  });
  h.poll.start(active);
  const reading = h.tick();
  h.poll.stop();
  finish(done);
  await reading;
  assert.deepEqual(h.values, []);
  assert.equal(h.pending, null);
  const hidden = harness(
    async () => {
      calls++;
      return done;
    },
    () => false,
  );
  hidden.poll.start(active);
  await hidden.tick();
  assert.equal(calls, 1);
  hidden.poll.stop();
});
test("read errors retain status and schedule only another read", async () => {
  const h = harness(async () => {
    throw new Error("offline");
  });
  h.poll.start(active);
  await h.tick();
  assert.equal(h.errors.length, 1);
  assert.equal(h.values.length, 0);
  assert.equal(h.delay, 2000);
  h.poll.stop();
});
