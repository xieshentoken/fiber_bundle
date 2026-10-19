import test from "node:test";
import assert from "node:assert/strict";
import { loadGraphScope } from "../../src/fiber_bundle/web/knowledge_graph_loading.js";
import { graphLoadFailure } from "../../src/fiber_bundle/web/knowledge_graph_setup.js";
const scope = { client_id: "c", vault_id: "v" };
const failing = (error) => async () => {
  throw error;
};
test("secondary failure leaves successful notes available and reports warning", async () => {
  const result = await loadGraphScope(
    {
      notes: async () => [{ note_id: "n" }],
      suggestions: failing(new Error("stale")),
      jobs: async () => [],
    },
    scope,
  );
  assert.equal(result.notes.length, 1);
  assert.equal(result.warnings.length, 1);
  assert.deepEqual(result.suggestions.items, []);
});
test("revoked permissions reject all stale notes", async () => {
  await assert.rejects(
    loadGraphScope(
      {
        notes: async () => [{ note_id: "n" }],
        suggestions: failing({ status: 403 }),
        jobs: async () => [],
      },
      scope,
    ),
  );
});
test("stale index blocks selector with actionable persistent message", async () => {
  const error = { code: "note_index_update_required" };
  await assert.rejects(
    loadGraphScope(
      {
        notes: failing(error),
        suggestions: async () => ({ items: [] }),
        jobs: async () => [],
      },
      scope,
    ),
  );
  assert.match(graphLoadFailure(error), /更新索引/);
  assert.match(
    graphLoadFailure({ code: "layout_rebind_required" }),
    /核对位置/,
  );
});
