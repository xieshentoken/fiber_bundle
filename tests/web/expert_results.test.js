import test from "node:test";
import assert from "node:assert/strict";
import {
  expertResults,
  reliableRows,
} from "../../src/fiber_bundle/web/expert_results.js";

test("ordinary notes never enter expert rendering or fetch image syntax", () => {
  assert.equal(
    expertResults(
      { answer: "![source](private.png) ![[image.png]]" },
      {
        read: () => assert.fail("ordinary image read"),
      },
    ),
    null,
  );
});
test("only independently verified rectangular rows can render a table", () => {
  assert.equal(reliableRows({ rows: [["a"], ["2"]] }), null);
  assert.equal(
    reliableRows({ rows_verified: true, rows: [["a", "b"], ["2"]] }),
    null,
  );
  assert.deepEqual(reliableRows({ rows_verified: true, rows: [["a"], [2]] }), [
    ["a"],
    [2],
  ]);
});
