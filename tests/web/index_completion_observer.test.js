import test from "node:test";
import assert from "node:assert/strict";
import { createIndexCompletionObserver } from "../../src/fiber_bundle/web/index_completion_observer.js";
const settle = async () => {
  for (let n = 0; n < 8; n++) await Promise.resolve();
};
const payload = (state) => ({ vaults: [{ vault_id: "test-vault", state }] });

test("one explicit update observes lightweight states and refreshes once after partial publication", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const fixed = { client_id: "test-client", vault_id: "test-vault" };
  let calls = 0,
    state = "observing",
    completed = 0;
  const observer = createIndexCompletionObserver(
    async () => {
      calls++;
      return payload(state);
    },
    () => fixed,
    async () => {
      completed++;
    },
  );
  observer.start(fixed);
  await settle();
  assert.equal(calls, 1);
  assert.equal(completed, 0);
  state = "needs_review";
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(calls, 2);
  assert.equal(completed, 1);
  t.mock.timers.tick(60000);
  await settle();
  assert.equal(calls, 2);
});

test("an uncertain observation retries only status reads and scope changes cannot refresh another vault", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let fixed = { vault_id: "test-vault" },
    calls = 0,
    completed = 0;
  const observer = createIndexCompletionObserver(
    async () => {
      calls++;
      throw Error("read busy");
    },
    () => fixed,
    async () => {
      completed++;
    },
  );
  observer.start(fixed);
  await settle();
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(calls, 2);
  fixed = { vault_id: "another-vault" };
  t.mock.timers.tick(5000);
  await settle();
  assert.equal(calls, 2);
  assert.equal(completed, 0);
  observer.stop();
});

test("cancelled or replaced observers abort their read and ignore delayed completion", async () => {
  const fixed = { vault_id: "test-vault" },
    reads = [];
  let completed = 0;
  const observer = createIndexCompletionObserver(
    ({ signal }) => new Promise((resolve) => reads.push({ signal, resolve })),
    () => fixed,
    async () => {
      completed++;
    },
  );
  observer.start(fixed);
  observer.start(fixed);
  assert.equal(reads[0].signal.aborted, true);
  reads[0].resolve(payload("ready"));
  await settle();
  assert.equal(completed, 0);
  reads[1].resolve(payload("ready"));
  await settle();
  assert.equal(completed, 1);
  observer.stop();
});
