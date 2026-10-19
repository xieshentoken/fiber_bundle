import test from "node:test";
import assert from "node:assert/strict";
import {
  documentState,
  runState,
  publicationState,
  shortId,
} from "../../src/fiber_bundle/web/state_labels.js";
import {
  pageCopy,
  pageParent,
} from "../../src/fiber_bundle/web/page_header.js";
test("unknown states and full identifiers remain inspectable", () => {
  assert.deepEqual(runState("future"), ["future", "info"]);
  assert.equal(shortId("abcdefgh-more"), "abcdefgh");
  assert.equal(documentState("extracted")[0], "已提取，待审核");
  assert.equal(publicationState("published")[0], "已发布");
});
test("review breadcrumb preserves original page ids", () => {
  assert.equal(pageParent("documents"), "worklist");
  assert.equal(pageParent("models"), null);
  assert.match(pageCopy("models").summary, /不代表当前可用/);
  assert.equal(pageCopy("home").summary, "");
});
