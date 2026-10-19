import test from "node:test";
import assert from "node:assert/strict";
import { nativeReply } from "../../src/fiber_bundle/executor/provider_bridge.mjs";

function collect(protocol, raw) {
  let output = "";
  const response = {
    writeHead() {},
    write(value) {
      output += value;
    },
    end(value = "") {
      output += value;
    },
  };
  nativeReply(response, protocol, { _native_response: raw }, true);
  return output;
}
test("Anthropic tool and thinking signature survive native SSE", () => {
  const stream = collect("anthropic-messages", {
    id: "synthetic",
    content: [
      { type: "thinking", thinking: "Synthetic", signature: "bound-signature" },
      {
        type: "tool_use",
        id: "call-1",
        name: "read",
        input: { path: "input.json" },
      },
    ],
    stop_reason: "tool_use",
    usage: { input_tokens: 4, output_tokens: 3 },
  });
  assert.match(stream, /bound-signature/);
  assert.match(stream, /input_json_delta/);
  assert.match(stream, /message_stop/);
  assert.match(stream, /call-1/);
});
test("Responses encrypted reasoning item survives completion events", () => {
  const stream = collect("openai-responses", {
    id: "synthetic",
    status: "completed",
    output: [
      {
        type: "reasoning",
        id: "reason-1",
        encrypted_content: "bound-encrypted-context",
        summary: [],
      },
      {
        type: "function_call",
        id: "fc-1",
        call_id: "call-1",
        name: "read",
        arguments: "{}",
      },
    ],
    usage: { input_tokens: 4, output_tokens: 3 },
  });
  assert.match(stream, /bound-encrypted-context/);
  assert.match(stream, /response.output_item.done/);
  assert.match(stream, /response.completed/);
});
