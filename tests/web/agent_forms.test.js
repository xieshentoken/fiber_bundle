import test from "node:test";
import assert from "node:assert/strict";
import { suggestTerms } from "../../src/fiber_bundle/web/agent_forms.js";
import { createAgentApi } from "../../src/fiber_bundle/web/agent_api.js";
import { describeTest } from "../../src/fiber_bundle/web/agent_test.js";

test("cloud test scope explicitly names the data boundary and never claims local processing", () => {
  const value = describeTest(
    {
      configuration: {
        vault_ids: ["one"],
        model_configuration_id: "opencodego-primary",
        limits: { timeout_seconds: 60 },
      },
    },
    [{ id: "one", name: "合成资料" }],
    {
      models: [
        {
          id: "opencodego-primary",
          name: "synthetic",
          endpoint: "https://opencode.ai/zen/go/v1/chat/completions",
          processing_location: "cloud",
        },
      ],
    },
  );
  assert.match(value, /合成资料/);
  assert.match(value, /OpenCodeGo 云端/);
  assert.match(value, /发送问题与这些库的检索片段/);
  assert.doesNotMatch(value, /本机处理/);
});

test("natural questions suggest bounded exact identifiers and explicit Chinese subjects", () => {
  assert.deepEqual(suggestTerms("请说明玄武温控器的温度"), ["玄武温控器"]);
  assert.deepEqual(suggestTerms("AB-12 与 AB-123 的区别？"), [
    "AB-12",
    "AB-123",
  ]);
  assert.deepEqual(suggestTerms("请解释“蓝鹊校准”在潮湿条件下的限制"), [
    "蓝鹊校准",
  ]);
  assert.deepEqual(
    suggestTerms(
      "请帮我综合评价这种方案适不适合用于生产线，以及如何才能提升所有指标？",
    ),
    [],
  );
  assert.equal(suggestTerms("AB-1 AB-2 AB-3").length, 2);
});

test("Agent API uses explicit bounded endpoints and preserves version/request bindings", async () => {
  const calls = [],
    request = async (...args) => {
      calls.push(args);
      return {};
    };
  const api = createAgentApi(request),
    body = { request_id: "one", expected_revision: 2, kind: "probe" };
  await api.start("id/other", body);
  await api.cancel("id", "op/path", {
    request_id: "cancel",
    expected_revision: 2,
  });
  await api.export("id");
  assert.deepEqual(calls[0], [
    "/admin/agent-connections/id%2Fother/operations",
    "POST",
    body,
  ]);
  assert.equal(
    calls[1][0],
    "/admin/agent-connections/id/operations/op%2Fpath/cancel",
  );
  assert.equal(calls[2][0], "/admin/agent-connections/id/configuration-export");
  assert.equal(JSON.stringify(calls).includes("token"), false);
});

test("registered providers and following default keep cloud disclosure accurate", () => {
  const item = {
    configuration: {
      vault_ids: [],
      model_configuration_id: "model_saved",
      limits: { timeout_seconds: 60 },
    },
  };
  const text = describeTest(item, [], {
    models: [
      {
        id: "model_saved",
        name: "Synthetic",
        provider: "anthropic",
        processing_location: "cloud",
      },
    ],
  });
  assert.match(text, /anthropic 云端/);
  assert.doesNotMatch(text, /OpenCodeGo/);
  item.configuration.model_configuration_id = "app-default";
  assert.match(
    describeTest(item, [], {
      models: [
        { id: "app-default", name: "默认", processing_location: "managed" },
      ],
    }),
    /提交时冻结应用默认模型.*云端/,
  );
});
