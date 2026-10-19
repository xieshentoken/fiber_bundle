// Container-only loopback server. Host communication is bounded JSONL over stdio.
import http from "node:http";
import readline from "node:readline";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { processingArgs } from "./processing_args.mjs";
import { nativeReply } from "./provider_bridge.mjs";
let protocol = "openai-completions";
let child,
  sequence = 0,
  ready = false;
const waiting = new Map();
const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");
const server = http.createServer(async (req, res) => {
  const requestPath = new URL(req.url, "http://127.0.0.1").pathname;
  if (
    req.method !== "POST" ||
    ![
      "/v1/chat/completions",
      "/v1/responses",
      "/v1/messages",
      "/backend/vaults",
      "/backend/article-capture",
      "/backend/document-extract",
    ].includes(requestPath)
  ) {
    res.writeHead(404).end();
    return;
  }
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 262144) {
      res.writeHead(413).end();
      return;
    }
    chunks.push(chunk);
  }
  let body;
  try {
    body = JSON.parse(Buffer.concat(chunks).toString());
  } catch {
    res.writeHead(400).end();
    return;
  }
  if (waiting.size >= 4) {
    res.writeHead(429).end();
    return;
  }
  const id = String(++sequence);
  waiting.set(id, { res, stream: body.stream === true });
  const capability = {
    "/backend/vaults": "vaults",
    "/backend/article-capture": "article_capture",
    "/backend/document-extract": "document_extract",
    "/v1/chat/completions": "model",
    "/v1/responses": "model",
    "/v1/messages": "model",
  }[requestPath];
  if (capability === "model" && protocol !== "openai-completions")
    body._fiber_native = protocol;
  emit({ type: capability, id, body });
});
function answer(frame) {
  const pending = waiting.get(frame.id);
  if (!pending) return;
  waiting.delete(frame.id);
  const { res, stream } = pending;
  if (!frame.ok) {
    res
      .writeHead(403, { "Content-Type": "application/json" })
      .end(JSON.stringify({ error: { message: "Host rejected request" } }));
    return;
  }
  if (protocol !== "openai-completions") {
    nativeReply(res, protocol, frame.body, stream);
    return;
  }
  if (!stream) {
    res
      .writeHead(200, { "Content-Type": "application/json" })
      .end(JSON.stringify(frame.body));
    return;
  }
  // Pi's existing OpenAI implementation handles messages/tool calls; preserve full provider usage.
  const data = frame.body,
    choice = data.choices[0];
  res.writeHead(200, { "Content-Type": "text/event-stream" });
  const base = {
    id: data.id,
    object: "chat.completion.chunk",
    created: data.created,
    model: data.model,
  };
  const delta = { ...choice.message };
  if (delta.tool_calls)
    delta.tool_calls = delta.tool_calls.map((t, index) => ({ ...t, index }));
  res.write(
    "data: " +
      JSON.stringify({
        ...base,
        choices: [{ index: 0, delta, finish_reason: null }],
      }) +
      "\n\n",
  );
  res.write(
    "data: " +
      JSON.stringify({
        ...base,
        choices: [{ index: 0, delta: {}, finish_reason: choice.finish_reason }],
        usage: data.usage,
      }) +
      "\n\n",
  );
  res.end("data: [DONE]\n\n");
}
async function init(frame) {
  if (ready) throw new Error("duplicate init");
  ready = true;
  protocol = frame.protocol || "openai-completions";
  await mkdir("/tmp/fiber/agent", { recursive: true });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  await writeFile(
    "/tmp/fiber/agent/models.json",
    JSON.stringify({
      providers: {
        "fiber-host": {
          baseUrl:
            protocol === "anthropic-messages" ? baseUrl : baseUrl + "/v1",
          api: protocol,
          apiKey: "container-placeholder",
          models: [
            {
              id: frame.model,
              name: frame.model,
              reasoning: Boolean(frame.effort && frame.effort !== "default"),
              input: ["text"],
              cost: {
                input: frame.inputPrice,
                output: frame.outputPrice,
                cacheRead: frame.inputPrice,
                cacheWrite: frame.inputPrice,
              },
              contextWindow: frame.context,
              maxTokens: frame.output,
              compat: {
                supportsStore: false,
                supportsReasoningEffort: Boolean(
                  frame.effort && frame.effort !== "default",
                ),
                maxTokensField: "max_tokens",
              },
            },
          ],
        },
      },
    }),
  );
  await writeFile(
    "/tmp/fiber/agent/settings.json",
    JSON.stringify({
      retry: { enabled: false },
      compaction: { enabled: false },
    }),
  );
  child = spawn(
    "/opt/fiber/node_modules/.bin/pi",
    [
      "--mode",
      "rpc",
      "--no-session",
      "--no-extensions",
      "--no-skills",
      "--no-prompt-templates",
      "--no-themes",
      ...(frame.taskMode === "processing"
        ? processingArgs(frame.processingKind)
        : frame.taskMode === "answer"
          ? ["--no-tools"]
          : [
              "--extension",
              frame.taskMode === "document"
                ? "/opt/fiber/document_tool.mjs"
                : frame.taskMode === "article"
                  ? "/opt/fiber/article_tool.mjs"
                  : "/opt/fiber/tool.mjs",
              "--tools",
              frame.taskMode === "document"
                ? "fiber_extract_document"
                : frame.taskMode === "article"
                  ? "fiber_capture_article"
                  : "read,write,edit,bash,fiber_vaults",
            ]),
      "--provider",
      "fiber-host",
      "--model",
      frame.model,
      "--thinking",
      frame.effort && frame.effort !== "default" ? frame.effort : "off",
    ],
    {
      cwd: "/task",
      env: {
        PATH: process.env.PATH,
        HOME: "/tmp/fiber",
        PI_CODING_AGENT_DIR: "/tmp/fiber/agent",
        FIBER_BRIDGE: baseUrl,
      },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  readline.createInterface({ input: child.stdout }).on("line", (line) => {
    try {
      emit({ type: "event", event: JSON.parse(line) });
    } catch {
      emit({ type: "protocol_error" });
    }
  });
  child.stderr.on("data", () => {}); // Never mirror arbitrary model/tool text to host logs.
  child.on("exit", (code) => {
    emit({ type: "exit", code });
    server.close();
    process.exit(code || 0);
  });
  child.stdin.write(
    JSON.stringify({ type: "prompt", message: frame.prompt, id: "task" }) +
      "\n",
  );
}
let input = Promise.resolve();
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  if (line.length > 3 * 1024 * 1024) {
    process.exit(2);
    return;
  }
  input = input
    .then(async () => {
      const f = JSON.parse(line);
      if (f.type === "init") await init(f);
      else if (f.type === "reply") answer(f);
      else if (f.type === "abort") child?.stdin.write('{"type":"abort"}\n');
    })
    .catch(() => process.exit(2));
});
process.stdin.on("end", () => {
  child?.kill();
  server.close();
  process.exit(0);
});
