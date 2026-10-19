import test from "node:test";
import assert from "node:assert/strict";
import {
  activityRows,
  activityCoverage,
} from "../../src/fiber_bundle/web/home_activity.js";
import { pendingItems } from "../../src/fiber_bundle/web/home_status.js";
test("running and saved automatic tasks never become inbox actions", () => {
  const records = {
    automatic: {
      value: [
        { id: "run", state: "queued" },
        { id: "saved", state: "complete", publication_state: "published" },
        { id: "failed", state: "failed" },
      ],
    },
  };
  assert.equal(pendingItems(records).length, 1);
  const rows = activityRows(records);
  assert.equal(rows.processing[0].id, "run");
  assert.equal(rows.history[0].status, "已保存");
  assert.equal(rows.history.length, 1);
});
test("a completed extraction needing review stays actionable, not completed history", () => {
  const records = {
    documents: { value: [{ id: "doc", state: "completed", title: "文档" }] },
  };
  assert.equal(pendingItems(records).length, 1);
  assert.equal(activityRows(records).history.length, 0);
});
test("cached successful source does not claim a fresh success after refresh failure", () => {
  const rows = activityRows({
    automatic: {
      value: [{ id: "a", state: "complete" }],
      error: { status: 503 },
    },
  });
  assert.equal(rows.history[0].status, "上次记录 · 需刷新");
  assert.deepEqual(activityRows({}), { processing: [], history: [] });
});
test("unknown or failed source coverage never represents a verified empty inbox", () => {
  assert.equal(activityCoverage({}), false);
  const records = Object.fromEntries(
    [
      "vaults",
      "batches",
      "documents",
      "media",
      "articles",
      "imports",
      "expert",
      "automatic",
      "videos",
      "channels",
      "executions",
    ].map((name) => [name, { value: [] }]),
  );
  assert.equal(activityCoverage(records), true);
  records.automatic.error = { status: 503 };
  assert.equal(activityCoverage(records), false);
});
