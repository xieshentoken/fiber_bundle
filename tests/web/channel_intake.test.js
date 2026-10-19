import test from "node:test";
import assert from "node:assert/strict";
import { createChannelApi } from "../../src/fiber_bundle/web/channel_api.js";
import { intakeLabel } from "../../src/fiber_bundle/web/channel_intake_records.js";

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
