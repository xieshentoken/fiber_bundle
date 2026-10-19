// Preserve provider-native blocks/signatures while serving bounded host replies as SSE.
export function nativeReply(res, protocol, body, streaming) {
  const raw = body._native_response;
  if (!raw) throw new Error("Missing native provider reply");
  if (!streaming) {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(raw));
    return;
  }
  res.writeHead(200, { "Content-Type": "text/event-stream" });
  const emit = (type, value) =>
    res.write(
      `event: ${type}\ndata: ${JSON.stringify({ type, ...value })}\n\n`,
    );
  if (protocol === "anthropic-messages") {
    emit("message_start", {
      message: { ...raw, content: [], stop_reason: null },
    });
    for (const [index, block] of raw.content.entries()) {
      emit("content_block_start", {
        index,
        content_block:
          block.type === "tool_use" ? { ...block, input: {} } : block,
      });
      if (block.type === "tool_use")
        emit("content_block_delta", {
          index,
          delta: {
            type: "input_json_delta",
            partial_json: JSON.stringify(block.input),
          },
        });
      emit("content_block_stop", { index });
    }
    emit("message_delta", {
      delta: { stop_reason: raw.stop_reason, stop_sequence: raw.stop_sequence },
      usage: raw.usage,
    });
    emit("message_stop", {});
  } else {
    emit("response.created", {
      response: { ...raw, output: [], status: "in_progress" },
    });
    for (const [output_index, item] of raw.output.entries()) {
      emit("response.output_item.added", { output_index, item });
      emit("response.output_item.done", { output_index, item });
    }
    emit(
      raw.status === "completed" ? "response.completed" : "response.incomplete",
      { response: raw },
    );
  }
  res.end();
}
