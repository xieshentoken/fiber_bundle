import test from "node:test";
import assert from "node:assert/strict";
import {
  batchProgress,
  createBatchProgressRefresh,
} from "../../src/fiber_bundle/web/batch_progress.js";

const batch = (state, rest = {}) => ({
  state: "selected",
  action: "save_one",
  store_state: "not_started",
  intake: { state, links: [], jobs: [], ...rest },
});
test("progress distinguishes queued, originals archived, extraction, publication and searchable note", () => {
  assert.match(batchProgress(batch("queued")).stage, /入库队列/);
  assert.match(
    batchProgress(batch("stored", { automatic_save: false })).stage,
    /自动保存尚未启用/,
  );
  assert.match(
    batchProgress(batch("stored", { links: [{ state: "extracting" }] })).stage,
    /提取/,
  );
  assert.match(
    batchProgress(
      batch("stored", { jobs: [{ state: "queued", phase: "publish" }] }),
    ).stage,
    /保存笔记/,
  );
  const complete = batchProgress(
    batch("stored", { jobs: [{ state: "complete", phase: "index" }] }),
  );
  assert.match(complete.stage, /已保存入库/);
  assert.equal(complete.active, false);
});
test("interrupted and unqueued previous choices offer an explicit continuation with useful errors", () => {
  assert.equal(batchProgress(batch("not_queued")).canContinue, true);
  const failed = batchProgress(
    batch("needs_review", { reason: "intake_archive_interrupted" }),
  );
  assert.equal(failed.canContinue, true);
  assert.match(failed.reasons[0], /中断/);
  const busy = batchProgress(
    batch("queued", { worker: { error: "intake_storage_busy" } }),
  );
  assert.match(busy.reasons[0], /存储暂忙/);
});
test("saved chat text reports missing images and article capacity failures remain specific", () => {
  const saved = batchProgress(
    batch("stored", {
      jobs: [{ state: "complete", phase: "index", missing_images: 1 }],
    }),
  );
  assert.match(saved.stage, /缺失图片已在笔记中标出/);
  assert.equal(saved.active, false);
  const failed = batchProgress(
    batch("stored", {
      links: [{ state: "needs_review", reason: "intake_article_image_limit" }],
    }),
  );
  assert.match(failed.reasons[0], /内部配置的图片上限/);
});
test("one visible-page timer refreshes progress and stops on completion or navigation", async () => {
  const timers = new Map();
  let next = 0,
    reads = 0;
  const progress = createBatchProgressRefresh(
    async () => {
      reads++;
    },
    assert.fail,
    {
      schedule(fn) {
        timers.set(++next, fn);
        return next;
      },
      cancel(id) {
        timers.delete(id);
      },
    },
  );
  progress.setVisible(true);
  for (let i = 0; i < 4; i++) progress.update([batch("queued")]);
  assert.equal(timers.size, 1);
  const [id, work] = [...timers][0];
  timers.delete(id);
  await work();
  assert.equal(reads, 1);
  assert.equal(timers.size, 1);
  progress.setVisible(false);
  assert.equal(timers.size, 0);
  progress.setVisible(true);
  progress.update([batch("stored", { jobs: [{ state: "complete" }] })]);
  assert.equal(timers.size, 0);
  progress.clear();
});
