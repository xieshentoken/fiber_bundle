import test from "node:test";
import assert from "node:assert/strict";
import {
  architecture,
  costSummary,
  costWindow,
  todoRows,
  homeStatus,
  homeVaults,
  vaultRows,
} from "../../src/fiber_bundle/web/home_model.js";
import { matches } from "../../src/fiber_bundle/web/command_palette.js";
const today = new Date("2026-10-14T03:00:00Z");
const daily = Array.from({ length: 60 }, (_, i) => ({
  day: new Date(Date.UTC(2026, 7, 16 + 31 + i)).toISOString().slice(0, 10),
  calls: 1,
  settled_calls: 1,
  unsettled_calls: 0,
  estimated_nusd: 100,
  reserved_nusd: 20,
}));
// Freeze exact dates rather than coupling the fixture to the wall clock.
daily.forEach(
  (d, i) =>
    (d.day = new Date(today.getTime() - (59 - i) * 86400000)
      .toISOString()
      .slice(0, 10)),
);
test("missing reads and configured models never claim runtime health", () => {
  assert.equal(architecture({}).nodes.review.tone, "idle");
  assert.equal(
    architecture({ query: { value: { configured: true } } }).nodes.model.tone,
    "info",
  );
  assert.equal(homeStatus({ vaults: { value: [] } }).tone, "info");
});
test("video tasks keep scoped destination and Chinese budget state", () => {
  const rows = todoRows({
    videos: {
      value: [
        { id: "v1", state: "waiting_budget", client_id: "c", vault_id: "v" },
      ],
    },
  });
  assert.equal(rows[0].status, "等待预算");
  assert.equal(rows[0].page, "media");
  assert.equal(rows[0].client_id, "c");
});
test("cached tasks report their own failed source without tainting fresh tasks", () => {
  const rows = todoRows({
    batches: {
      value: [{ id: "b1", state: "frozen", preview: "cached batch" }],
      error: { status: 503, code: "storage_unavailable" },
    },
    documents: { value: [{ id: "d1", state: "needs_review" }] },
    agents: { error: { status: 503, code: "storage_unavailable" } },
  });
  assert.equal(rows.find((r) => r.id === "b1").status, "上次记录 · 需刷新");
  assert.equal(rows.find((r) => r.id === "d1").status, "待审核");
});
test("sixty-day report produces two complete windows, never treats missing days as zero", () => {
  assert.deepEqual(costWindow(today), {
    start: "2026-08-16",
    end: "2026-10-14",
  });
  const all = costSummary({ daily, local_calls: 37 }, today);
  assert.equal(all.total, 3000);
  assert.equal(all.calls, 30);
  assert.equal(all.change, 0);
  assert.equal(all.localCalls, 37);
  const partial = costSummary({ daily: daily.slice(-7) }, today);
  assert.equal(partial.total, null);
  assert.equal(partial.change, null);
  assert.equal(partial.complete, false);
});
test("unsettled prices suppress comparisons and remain unknown", () => {
  const rows = daily.map((d) => ({
    ...d,
    settled_calls: 0,
    unsettled_calls: 1,
  }));
  const summary = costSummary({ daily: rows }, today);
  assert.equal(summary.allUnknown, true);
  assert.equal(summary.unsettled, 30);
  assert.equal(summary.change, null);
});
test("shortcut search is local and case-insensitive", () => {
  assert(matches({ label: "资料导入", keywords: "IMPORT" }, "import"));
  assert(!matches({ label: "消息入口" }, "预算"));
});

test("automatic exceptions retain their vault destination and contribute to review status", () => {
  const records = {
    documents: { value: [] },
    media: { value: [] },
    articles: { value: [] },
    automatic: {
      value: [
        {
          id: "a1",
          kind: "document",
          state: "needs_review",
          vault_id: "v1",
          client_id: "c1",
        },
      ],
    },
  };
  const rows = todoRows(records);
  assert.equal(rows[0].vault_id, "v1");
  assert.equal(rows[0].title, "文档提取");
  assert.equal(architecture(records).nodes.review.tone, "waiting");
  assert.match(architecture(records).nodes.review.text, /1/);
});
test("month costs distinguish unknown pricing from a known zero", () => {
  const unknown = costSummary(
    {
      daily: daily.map((d) => ({
        ...d,
        estimated_nusd: 0,
        settled_calls: 0,
        unsettled_calls: 1,
      })),
    },
    today,
  );
  assert.equal(unknown.monthAllUnknown, true);
  assert.ok(unknown.monthUnknown > 0);
  const free = costSummary(
    { daily: daily.map((d) => ({ ...d, estimated_nusd: 0 })) },
    today,
  );
  assert.equal(free.monthAllUnknown, false);
  assert.equal(free.monthUnknown, 0);
});

test("automatic history, failures, saved indexing and human confirmation stay distinct", () => {
  const jobs = [
    {
      id: "old",
      state: "needs_review",
      display_state: "superseded",
      superseded_by: "new",
    },
    { id: "new", state: "complete", display_state: "complete" },
    {
      id: "saved",
      state: "needs_review",
      display_state: "index_reconcile",
      publication_state: "published",
      title: "保存的文章",
      display_message: "保存已完成，索引需要核对",
      vault_id: "v",
      client_id: "c",
    },
    {
      id: "failed",
      state: "needs_review",
      display_state: "failed",
      display_message: "模型超时，旧费用保留",
    },
    { id: "manual", state: "reviewed", display_state: "awaiting_review" },
    { id: "budget", state: "needs_review", display_state: "waiting_budget" },
    {
      id: "environment",
      state: "needs_review",
      display_state: "waiting_dependency",
    },
    { id: "cancelled", state: "cancelled" },
  ];
  const records = { automatic: { value: jobs } };
  const rows = todoRows(records);
  assert.deepEqual(
    rows.map((r) => r.id),
    ["saved", "failed", "manual", "budget", "environment"],
  );
  assert.equal(rows[0].status, "已保存 · 索引待核对");
  assert.equal(rows[0].title, "保存的文章");
  assert.match(rows[0].detail, /索引需要核对/);
  assert.equal(rows[0].vault_id, "v");
  assert.equal(rows[1].status, "处理失败");
  assert.equal(rows[1].tone, "error");
  assert.match(rows[1].detail, /模型超时/);
  assert.equal(rows[2].status, "待确认保存");
  assert.equal(rows[3].status, "等待预算");
  assert.equal(rows[4].status, "等待处理环境");
});

test("home excludes trashed/detached vault history from cards and index counts", () => {
  const vaults = [
    {
      id: "p",
      name: "personal",
      status: "active",
      attachment_state: "trashed",
    },
    {
      id: "s",
      name: "supplier",
      status: "active",
      attachment_state: "detached",
    },
    { id: "l", name: "L2", status: "active", attachment_state: "attached" },
    {
      id: "r",
      name: "recover",
      status: "active",
      attachment_state: "reconcile",
    },
    { id: "a", name: "approval", status: "pending" },
    { id: "x", name: "rejected", status: "rejected" },
  ];
  assert.deepEqual(
    homeVaults(vaults).map((v) => v.id),
    ["l", "r", "a"],
  );
  const records = {
    vaults: { value: vaults },
    index: {
      value: {
        vaults: [
          { vault_id: "p", generation_state: "unavailable" },
          { vault_id: "s", generation_state: "unavailable" },
          { vault_id: "l", generation_state: "ready", indexed_notes: 7 },
        ],
      },
    },
  };
  assert.deepEqual(
    vaultRows(records).map((v) => v.id),
    ["l", "r", "a"],
  );
  assert.equal(architecture(records).nodes.index.text, "1/1 库快照就绪");
  assert.equal(
    vaultRows({ vaults: { value: vaults, error: { status: 503 } } })[0].status,
    "上次记录 · 需刷新",
  );
});

test("lifetime usage stays independent of recent window and missing lifetime stays unknown", () => {
  const lifetime = {
    calls: 10,
    input_tokens: 9000,
    output_tokens: 1000,
    token_unknown_calls: 2,
    estimated_nusd: 2000,
  };
  const result = costSummary(
    {
      daily: daily.map((d) => ({
        ...d,
        input_tokens: 100,
        output_tokens: 20,
        token_unknown_calls: 0,
      })),
      lifetime,
    },
    today,
  );
  assert.deepEqual(result.lifetime, lifetime);
  assert.equal(result.inputTokens, 3000);
  assert.equal(result.outputTokens, 600);
  assert.equal(costSummary({ daily }, today).lifetime, null);
});
