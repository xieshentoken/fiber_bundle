import test from "node:test";
import assert from "node:assert/strict";
import {
  knownCost,
  ledgerCost,
  summaryCost,
} from "../../src/fiber_bundle/web/atlas/usage_model.js";
import { todoRows } from "../../src/fiber_bundle/web/atlas/home_model.js";
import { translate } from "../../src/fiber_bundle/web/i18n/index.js";
test("Atlas unknown pricing remains distinct from known zero and incomplete reads", () => {
  const unknown = {
    calls: 3,
    settled_calls: 0,
    unsettled_calls: 3,
    estimated_nusd: 0,
  };
  assert.equal(knownCost(unknown), null);
  assert.match(ledgerCost(unknown), /未知|Unknown/);
  assert.equal(
    ledgerCost({ ...unknown, calls: 0, unsettled_calls: 0 }),
    "$0.00",
  );
  assert.equal(ledgerCost({ ...unknown, available: false }), "—");
  assert.match(
    summaryCost({
      complete: true,
      total: 0,
      calls: 324,
      allUnknown: true,
      unsettled: 324,
    }),
    /未知|Unknown/,
  );
  assert.match(
    ledgerCost({ ...unknown, settled_calls: 1 }),
    /部分未知|partly unknown/,
  );
});
test("Atlas automatic pending rows retain article title, saved/index reconciliation and reason", () => {
  const rows = todoRows({
    automatic: {
      value: [
        {
          id: "synthetic",
          title: "合成文章标题",
          kind: "intake",
          display_state: "index_reconcile",
          state: "complete",
          publication_state: "published",
          vault_id: "v",
        },
      ],
    },
    vaults: { value: [{ id: "v", name: "合成库" }] },
  });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].title, "合成文章标题");
  assert.equal(rows[0].status[0], translate("已保存 · 索引待核对"));
  assert.equal(rows[0].status[1], "error");
});
