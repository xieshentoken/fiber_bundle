import test from "node:test";
import assert from "node:assert/strict";
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
const { createChannelApi } =
  await import("../../src/fiber_bundle/web/channel_api.js");
const { intakeLabel } =
  await import("../../src/fiber_bundle/web/channel_intake_records.js");

test("intake actions preserve CAS and encode original/review resource identities", async () => {
  const calls = [],
    api = createChannelApi(async (...args) => {
      calls.push(args);
      return {};
    });
  await api.channelIntakeSave("entry/a", {
    expected_revision: 3,
    settings: { enabled: false, sites: [] },
  });
  await api.channelIntakeBatches("entry/a", {
    expected_revision: 4,
    batch_ids: ["batch/b"],
  });
  await api.channelIntakePublish("entry/a", "batch/b", 0, "review-id");
  assert.deepEqual(
    calls.map((c) => c.slice(0, 3)),
    [
      [
        "/admin/channels/entry%2Fa/intake",
        "PUT",
        {
          expected_revision: 3,
          settings: { enabled: false, sites: [] },
        },
      ],
      [
        "/admin/channels/entry%2Fa/intake/batches",
        "POST",
        {
          expected_revision: 4,
          batch_ids: ["batch/b"],
        },
      ],
      [
        "/admin/channels/entry%2Fa/intake/batches/batch%2Fb/links/0/publish",
        "POST",
        {
          review_id: "review-id",
        },
      ],
    ],
  );
  assert.equal(
    api.channelIntakeOriginalURL("entry/a", "batch/b", "asset/c"),
    "/admin/channels/entry%2Fa/intake/batches/batch%2Fb/originals/asset%2Fc",
  );
  assert.equal(
    api.channelIntakeAssetURL("entry/a", "batch/b", 0, "images/合成 图.png"),
    "/admin/channels/entry%2Fa/intake/batches/batch%2Fb/links/0/assets/images/%E5%90%88%E6%88%90%20%E5%9B%BE.png",
  );
});

test("metadata and raw archival never claim searchable publication or full coverage", () => {
  assert.match(intakeLabel("metadata_only"), /正文待补全/);
  assert.match(intakeLabel("stored"), /原件已归档/);
  assert.match(intakeLabel("review_required"), /待审核/);
  assert.match(intakeLabel("unknown"), /手机核对/);
  assert.match(intakeLabel("intake_site_not_allowed"), /整批保留/);
  assert.doesNotMatch(
    intakeLabel("PRIVATE_EXTERNAL_BODY"),
    /PRIVATE_EXTERNAL_BODY/,
  );
});

test("new Pi attempt approval is explicit and absent from ordinary retry", async () => {
  const calls = [],
    api = createChannelApi(async (...args) => calls.push(args));
  await api.channelIntakeRetry("channel", "batch", 0, 4);
  await api.channelIntakeRetry("channel", "batch", 0, 4, true);
  assert.deepEqual(calls[0][2], { expected_revision: 4 });
  assert.deepEqual(calls[1][2], {
    expected_revision: 4,
    approve_new_model_attempt: true,
  });
});

test("refreshing unstarted video settings is explicit and independent of paid retry", async () => {
  const calls = [];
  const api = createChannelApi(async (...args) => {
    calls.push(args);
    return {};
  });
  await api.channelIntakeRetry("entry", "batch", 0, 6);
  await api.channelIntakeRetry("entry", "batch", 0, 6, false, true);
  assert.deepEqual(calls[0][2], { expected_revision: 6 });
  assert.deepEqual(calls[1][2], {
    expected_revision: 6,
    use_current_video_settings: true,
  });
  assert.match(
    intakeLabel("intake_video_source_tls_interrupted"),
    /安全连接中断/,
  );
});

test("Atlas video stages and connection failures have specific bilingual labels", async () => {
  const labels =
    await import("../../src/fiber_bundle/web/atlas/channel_labels.js");
  assert.match(labels.intakeLabel("resolve_source"), /视频短链接/);
  assert.match(labels.intakeLabel("visual_analysis"), /分析选择帧/);
  assert.match(
    labels.intakeLabel("intake_video_source_tls_interrupted"),
    /安全连接中断/,
  );
  assert.equal(labels.intakeLabel("published"), "已保存入库");
});

test("video token budget and progress survive API transport in either locale", async () => {
  const { videoProgressLines } =
    await import("../../src/fiber_bundle/web/video_intake_status.js");
  const { setLang } = await import("../../src/fiber_bundle/web/atlas/i18n.js");
  const calls = [];
  const api = createChannelApi(async (...args) => calls.push(args));
  await api.channelIntakeRetry("channel", "batch", 0, 4, true, false, {
    max_video_tokens: 500000,
  });
  assert.equal(calls[0][2].max_video_tokens, 500000);
  assert.equal(calls[0][2].max_new_model_calls, undefined);
  const link = {
    video_progress: {
      frames_complete: 18,
      frames_total: 104,
      frames_remaining: 86,
      failed_frame: "F0019",
      token_budget: {
        limit: 500000,
        used_tokens: 12000,
        remaining_tokens: 488000,
        reserved_tokens: 2000,
      },
    },
  };
  setLang("en");
  assert.match(
    videoProgressLines(link).join(" "),
    /18\/104.*F0019.*86 frames.*12,000 \/ 500,000.*488,000.*2,000/s,
  );
  setLang("zh");
  assert.match(
    videoProgressLines(link).join(" "),
    /18\/104.*剩余 86 帧.*12,000 \/ 500,000.*488,000.*2,000/s,
  );
});
