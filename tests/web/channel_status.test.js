import test from "node:test";
import assert from "node:assert/strict";
import {
  channelStatusText,
  channelLabel,
  intakeStatusText,
  receiptStatusText,
} from "../../src/fiber_bundle/web/channel_status.js";
import { createChannelApi } from "../../src/fiber_bundle/web/channel_api.js";

function entry() {
  return {
    id: "synthetic",
    name: "合成入口",
    origin: "gui",
    platform: "feishu",
    binding_state: "bound",
    revision: 1,
    configuration_sha256: "a".repeat(64),
    observed_at: 100,
    configuration: { id: "synthetic", settings: { answer_mode: "evidence" } },
    runtime: { process_state: "running", heartbeat_fresh: true },
    transport: "connected",
    capabilities: Object.fromEntries(
      ["query", "model", "capture", "download", "store"].map((name) => [
        name,
        { enabled: true, available: name === "query" },
      ]),
    ),
  };
}
test("process and platform connection do not imply query capability", () => {
  const value = entry();
  value.capabilities.query.available = false;
  assert.deepEqual(channelStatusText(value), {
    binding: "已绑定",
    process: "运行中",
    transport: "已连接",
    query: "暂不可用",
  });
});
test("stale observation removes positive process transport and capability claims", () => {
  const text = channelStatusText(entry(), true);
  assert.equal(text.process, "状态已过期");
  assert.equal(text.transport, "状态已过期，待刷新");
  assert.equal(text.query, "待刷新");
});
test("unknown diagnostic codes never become success or leak server strings", () => {
  assert.equal(channelLabel("unexpected PRIVATE_DETAIL"), "待核对");
  assert.match(
    channelLabel("channel_delivery_requires_reconciliation"),
    /在手机确认/,
  );
  assert.match(channelLabel("channel_credentials_rejected"), /凭证验证失败/);
});
test("intake reports its own blocker and unresolved batches despite a connected receiver", () => {
  const value = entry();
  value.intake = {
    enabled: true,
    reason: "automatic_policy_layout_changed",
    pending_review: 5,
    worker: { state: "ready" },
  };
  assert.match(intakeStatusText(value), /保存规则的目录已变化/);
  assert.match(intakeStatusText(value), /5 批待恢复/);
  assert.doesNotMatch(intakeStatusText(value), /已就绪/);
  assert.match(intakeStatusText(value, true), /状态已过期/);
  value.intake = {
    enabled: true,
    worker: { state: "processing" },
    pending_review: 0,
  };
  assert.match(intakeStatusText(value), /正在处理资料/);
});
test("channel API validates observable status contracts before rendering", async () => {
  const value = {
    schema: "channel-overview/v1",
    observed_at: 100,
    channels: [entry()],
    issues: [],
  };
  const api = createChannelApi(async (_path, _method, _body, validate) => {
    if (!validate(value)) throw Error("invalid status contract");
    return value;
  });
  assert.equal((await api.channelOverview()).channels.length, 1);
  value.channels[0].capabilities = null;
  await assert.rejects(api.channelOverview(), /invalid status/);
});
test("lifecycle HTTP operations encode identities and preserve preview-bound confirmation", async () => {
  const calls = [],
    api = createChannelApi(async (...args) => {
      calls.push(args);
      return {};
    });
  await api.channelLifecyclePreview("a/b", "remove");
  await api.channelLifecycle("a/b", { action: "remove", expected_revision: 2 });
  await api.channelLifecycleResume("a/b", "op/id");
  assert.deepEqual(
    calls.map((call) => call.slice(0, 3)),
    [
      ["/admin/channels/a%2Fb/lifecycle/preview", "POST", { action: "remove" }],
      [
        "/admin/channels/a%2Fb/lifecycle",
        "POST",
        { action: "remove", expected_revision: 2 },
      ],
      ["/admin/channels/a%2Fb/lifecycle/op%2Fid/resume", "POST"],
    ],
  );
});

test("Weixin receive diagnostics distinguish recovery from local save failures", () => {
  assert.match(channelLabel("reconnecting"), /正在重连/);
  assert.match(channelLabel("channel_weixin_poll_exhausted"), /无需重复发送/);
  assert.match(channelLabel("channel_weixin_response_invalid"), /响应格式异常/);
  assert.doesNotMatch(
    channelLabel("channel_weixin_poll_rejected"),
    /请重新登录/,
  );
});

test("ambiguous receipts stay visible without claiming new batches are blocked", () => {
  const value = entry();
  value.receipts = {
    enabled: true,
    pending_batches: 3,
    needs_review_batches: 1,
    sending_batches: 1,
  };
  const text = String(receiptStatusText(value));
  assert.match(text, /1 批回执待核对/);
  assert.match(text, /不会自动重发/);
  assert.match(text, /1 批回执发送待确认/);
  assert.match(text, /3 批回执待发送或更新/);
  assert.match(text, /新批次回执独立发送/);
  assert.match(String(receiptStatusText(value, true)), /状态已过期/);
  value.receipts.enabled = false;
  assert.match(String(receiptStatusText(value)), /新回执已关闭/);
  assert.doesNotMatch(String(receiptStatusText(value)), /独立发送/);
  value.transport = "disconnected";
  value.receipts.enabled = true;
  assert.match(String(receiptStatusText(value)), /连接恢复后/);
});

test("channel API rejects malformed receipt counters and accepts older servers", async () => {
  const value = {
    schema: "channel-overview/v1",
    observed_at: 100,
    channels: [entry()],
    issues: [],
  };
  const api = createChannelApi(async (_path, _method, _body, validate) => {
    if (!validate(value)) throw Error("invalid receipt contract");
    return value;
  });
  await api.channelOverview();
  value.channels[0].receipts = {
    enabled: true,
    pending_batches: -1,
    needs_review_batches: 0,
    sending_batches: 0,
  };
  await assert.rejects(api.channelOverview(), /invalid receipt/);
  value.channels[0].receipts.pending_batches = 0;
  await api.channelOverview();
});
