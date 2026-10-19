import test from "node:test";
import assert from "node:assert/strict";
import { receiptSummary } from "../../src/fiber_bundle/web/inventory_summary.js";
test("receipts remain distinguishable without names and do not claim publication", () => {
  const first = receiptSummary({ job_id: "first-record-1", state: "stored" });
  const second = receiptSummary({ job_id: "second-record-2", state: "stored" });
  assert.notEqual(first.title, second.title);
  assert.equal(first.state, "原件已收到");
  assert.doesNotMatch(first.state, /笔记已|入库|索引已/);
});
test("user filenames and unknown evidence are preserved", () => {
  assert.equal(
    receiptSummary({ filename: "已完成.md", state: "future" }).title,
    "已完成.md",
  );
  assert.equal(receiptSummary({ state: "future" }).state, "future");
});
