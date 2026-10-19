import test from "node:test";
import assert from "node:assert/strict";
import { agentErrorText } from "../../src/fiber_bundle/web/agent_api.js";
import {
  agentError,
  operationState,
  operationTitle,
} from "../../src/fiber_bundle/web/agent_labels.js";
test("operation labels distinguish a queued test from completion", () => {
  assert.equal(operationTitle("test_query"), "问答测试");
  assert.equal(operationState("queued"), "已排队");
  assert.equal(operationState("running"), "正在执行");
  assert.equal(operationState("succeeded"), "已完成");
  assert.equal(operationState("unexpected"), "状态待核对");
});
test("conflict feedback preserves edits and asks for explicit comparison", () => {
  assert.match(agentError("agent_revision_conflict"), /编辑已保留/);
  assert.match(agentError("agent_revision_conflict"), /核对最新配置/);
  assert.match(
    agentError("agent_revision_conflict"),
    /agent_revision_conflict/,
  );
});
test("provider failure preserves diagnostics without inventing credential or retry behavior", () => {
  const message = agentError("agent_provider_http_500");
  assert.match(message, /不会自动重试/);
  assert.match(message, /agent_provider_http_500/);
  assert.doesNotMatch(message, /密码错误|凭据错误|已重试/);
  assert.match(agentError("agent_unknown_fault"), /agent_unknown_fault/);
  assert.equal(agentError("请填写明确术语。"), "请填写明确术语。");
  assert.equal(agentErrorText("unknown provider message"), undefined);
});

test("changed evidence and failed generation explain non-delivery without retry", () => {
  assert.match(
    agentError("agent_evidence_changed"),
    /不再交付.*agent_evidence_changed/,
  );
  assert.match(
    agentError("agent_generation_failed"),
    /不会自动重试.*agent_generation_failed/,
  );
});
