import test from "node:test";
import assert from "node:assert/strict";
import { createAdminApi } from "../../src/fiber_bundle/web/api.js";
import { createSession } from "../../src/fiber_bundle/web/session.js";
import { graphLoadFailure } from "../../src/fiber_bundle/web/knowledge_graph_setup.js";

test("index layout conflict gives a repair path and retains the precise error code", async () => {
  const api = createAdminApi(
    createSession(),
    {
      onConnection() {},
      onUnauthorized() {},
    },
    async () => ({
      ok: false,
      status: 409,
      json: async () => ({ error: "layout_rebind_required" }),
    }),
  );
  await assert.rejects(
    api.graph.updateIndex({ client_id: "c", vault_id: "v" }),
    (e) => {
      assert.equal(e.code, "layout_rebind_required");
      assert.match(e.message, /批量核对外部改动/);
      assert.doesNotMatch(e.message, /事件内容冲突/);
      assert.match(graphLoadFailure(e), /批量核对外部改动/);
      return true;
    },
  );
});

test("only an actual event conflict uses event-conflict wording", async () => {
  for (const code of ["event_conflict", "unknown_conflict"]) {
    const api = createAdminApi(
      createSession(),
      {
        onConnection() {},
        onUnauthorized() {},
      },
      async () => ({
        ok: false,
        status: 409,
        json: async () => ({ error: code }),
      }),
    );
    await assert.rejects(api.graph.updateIndex({}), (e) => {
      assert.equal(
        e.message.includes("事件内容冲突"),
        code === "event_conflict",
      );
      return true;
    });
  }
});

test("ordinary note replacement points to identity repair rather than storage failure", async () => {
  const api = createAdminApi(
    createSession(),
    { onConnection() {}, onUnauthorized() {} },
    async () => ({
      ok: false,
      status: 409,
      json: async () => ({ error: "layout_tracked_note_replaced" }),
    }),
  );
  await assert.rejects(api.graph.updateIndex({}), (error) => {
    assert.match(error.message, /普通笔记或 Map/);
    assert.match(graphLoadFailure(error), /保留原身份/);
    assert.doesNotMatch(error.message, /存储不可用/);
    return true;
  });
});
