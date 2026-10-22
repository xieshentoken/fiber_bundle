import test from "node:test";
import assert from "node:assert/strict";
import { relationLines } from "../../src/fiber_bundle/web/channel_query_scope.js";
const scope = {
  vaults: [
    { vault_id: "a", alias: "个人", name: "Personal", available: true },
    { vault_id: "b", alias: "供应商", name: "Suppliers", available: false },
  ],
};
test("query relationships explicitly show the union and exact alias destinations", () => {
  const lines = relationLines({
    query_scope: scope,
    configuration: { settings: { purpose: "query", add: false } },
  });
  assert.match(lines[0], /个人.*Personal.*供应商.*Suppliers/);
  assert(
    lines.some((s) => s.includes("查询[供应商]：问题 → Suppliers（不可用）")),
  );
  assert(lines.every((s) => !s.includes("独立入库")));
});
test("saving relationship uses the actual intake target and does not imply union reads", () => {
  const lines = relationLines({
    query_scope: scope,
    vault_id: "a",
    intake: { target_name: "Suppliers" },
    configuration: { settings: { purpose: "intake", add: true } },
  });
  assert.deepEqual(lines, ["独立入库 → Suppliers"]);
});
