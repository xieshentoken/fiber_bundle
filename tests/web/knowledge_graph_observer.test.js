import test from "node:test";
import assert from "node:assert/strict";
import { createTaskObserver } from "../../src/fiber_bundle/web/knowledge_graph_observer.js";
function clock() {
  let time = 0;
  const pending = new Map();
  let id = 0;
  return {
    now: () => time,
    interval: 5,
    schedule: (f, delay) => {
      pending.set(++id, { f, at: time + delay });
      return id;
    },
    cancel: (i) => pending.delete(i),
    async tick(t = 10) {
      time += t;
      const next = [...pending]
        .sort((a, b) => a[1].at - b[1].at)
        .find(([, v]) => v.at <= time);
      if (next) {
        pending.delete(next[0]);
        await next[1].f();
      }
    },
    get size() {
      return pending.size;
    },
  };
}
test("only running tasks are read; completed, failed, cancelled all stop without mutations", async () => {
  for (const state of [
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "stale",
  ]) {
    const c = clock(),
      seen = [],
      stops = [];
    let reads = 0;
    const o = createTaskObserver({
      ...c,
      timeout: 100,
      read: async () => {
        reads++;
        return [{ id: "job", state }];
      },
      onUpdate: (rows, ids) => seen.push([rows[0].state, ids]),
      onStop: (r) => stops.push(r),
    });
    o.start([{ id: "queued", state: "queued" }]);
    assert.equal(c.size, 0);
    o.start([{ id: "job", state: "running" }]);
    await c.tick();
    assert.equal(reads, 1);
    assert.deepEqual(seen, [[state, ["job"]]]);
    assert.deepEqual(stops, ["terminal"]);
    assert.equal(c.size, 0);
  }
});
test("timeout, permission error, and missing task terminate bounded polling", async () => {
  const c = clock(),
    stops = [];
  let reads = 0;
  const o = createTaskObserver({
    ...c,
    timeout: 15,
    read: async () => {
      reads++;
      return [{ id: "a", state: "running" }];
    },
    onUpdate: () => {},
    onStop: (r) => stops.push(r),
  });
  o.start([{ id: "a", state: "running" }]);
  await c.tick(10);
  await c.tick(10);
  assert.equal(reads, 1);
  assert.deepEqual(stops, ["timeout"]);
  assert.equal(c.size, 0);
  for (const kind of ["error", "unavailable"]) {
    const d = clock(),
      stop = [];
    const observer = createTaskObserver({
      ...d,
      read: async () => {
        if (kind === "error") throw new Error("403");
        return [];
      },
      onUpdate: () => {},
      onStop: (r) => stop.push(r),
    });
    observer.start([{ id: "a", state: "running" }]);
    await d.tick();
    assert.deepEqual(stop, [kind]);
    assert.equal(d.size, 0);
  }
});
test("scope/leave cancellation discards a late response and stops scheduled polls", async () => {
  const c = clock();
  let resolve,
    updates = 0;
  const o = createTaskObserver({
    ...c,
    read: () => new Promise((r) => (resolve = r)),
    onUpdate: () => updates++,
    onStop: () => {},
  });
  o.start([{ id: "a", state: "running" }]);
  const tick = c.tick();
  o.stop();
  resolve([{ id: "a", state: "completed" }]);
  await tick;
  assert.equal(updates, 0);
  assert.equal(c.size, 0);
  o.start([{ id: "a", state: "running" }]);
  o.stop();
  await c.tick();
  assert.equal(updates, 0);
});

test("explicitly admitted launch observes its queued startup gap without starting anything", async () => {
  const c = clock(),
    stops = [],
    updates = [];
  let index = 0;
  const o = createTaskObserver({
    ...c,
    timeout: 100,
    read: async () => [
      { id: "a", state: ["queued", "running", "completed"][index++] },
    ],
    onUpdate: (r) => updates.push(r[0].state),
    onStop: (r) => stops.push(r),
  });
  o.start([{ id: "a", state: "launch_requested" }]);
  await c.tick();
  await c.tick();
  await c.tick();
  assert.deepEqual(updates, ["queued", "running", "completed"]);
  assert.deepEqual(stops, ["terminal"]);
  assert.equal(c.size, 0);
});

test("deadline stops and discards a read that is still in flight", async () => {
  const c = clock(),
    stops = [];
  let resolve,
    updates = 0;
  const o = createTaskObserver({
    ...c,
    timeout: 100,
    read: () => new Promise((r) => (resolve = r)),
    onUpdate: () => updates++,
    onStop: (r) => stops.push(r),
  });
  o.start([{ id: "a", state: "running" }]);
  const inFlight = c.tick();
  await c.tick(100);
  assert.deepEqual(stops, ["timeout"]);
  assert.equal(c.size, 0);
  resolve([{ id: "a", state: "completed" }]);
  await inFlight;
  assert.equal(updates, 0);
});
