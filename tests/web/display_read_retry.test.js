import test from "node:test";
import assert from "node:assert/strict";
import { createAdminApi } from "../../src/fiber_bundle/web/api.js";
import { createSession } from "../../src/fiber_bundle/web/session.js";

const settle = async () => {
  for (let count = 0; count < 12; count++) await Promise.resolve();
};

for (const recover of [false, true]) {
  test(`a changed display GET retries once; recovery=${recover}`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    let calls = 0;
    const api = createAdminApi(
      createSession(),
      { onConnection() {}, onUnauthorized() {} },
      async (_, options) => {
        assert.equal(options.method, "GET");
        calls++;
        return calls === 2 && recover
          ? { ok: true, json: async () => ({ state: "ready", notes: 60 }) }
          : {
              ok: false,
              status: 503,
              json: async () => ({ error: "display_changed" }),
            };
      },
    );
    const pending = api.noteIndexStatus("client", "vault");
    const result = recover
      ? pending
      : assert.rejects(pending, (error) => error.code === "display_changed");
    await settle();
    assert.equal(calls, 1);
    t.mock.timers.tick(1000);
    await settle();
    const value = await result;
    if (recover) assert.equal(value.state, "ready");
    t.mock.timers.tick(60000);
    await settle();
    assert.equal(calls, 2);
  });
}

test("a mutation is never repeated for a display-like busy error", async () => {
  let calls = 0;
  const api = createAdminApi(
    createSession(),
    { onConnection() {}, onUnauthorized() {} },
    async (_, options) => {
      assert.equal(options.method, "POST");
      calls++;
      return {
        ok: false,
        status: 503,
        json: async () => ({ error: "display_changed" }),
      };
    },
  );
  await assert.rejects(
    api.updateNoteIndex({ client_id: "client", vault_id: "vault" }),
    (error) => error.code === "display_changed",
  );
  assert.equal(calls, 1);
});
