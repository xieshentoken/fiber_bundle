// Dedicated Pi draft capability. Stdio host approval is required for every model request.
import http from "node:http";
import readline from "node:readline";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
let child,
  initialized = false,
  count = 0,
  pending;
const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");
const fatal = () => {
  child?.kill();
  server.close();
  process.exit(2);
};
const server = http.createServer(async (req, res) => {
  if (
    req.method !== "POST" ||
    req.url !== "/v1/chat/completions" ||
    pending ||
    count >= 2
  ) {
    res.writeHead(403).end();
    return;
  }
  const chunks = [];
  let size = 0;
  try {
    for await (const chunk of req) {
      size += chunk.length;
      if (size > 256 * 1024) {
        res.writeHead(413).end();
        return;
      }
      chunks.push(chunk);
    }
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    const id = String(++count);
    pending = { id, res, stream: body.stream === true };
    emit({ type: "model", id, body });
  } catch {
    res.writeHead(400).end();
  }
});
function reply(frame) {
  if (
    !pending ||
    frame.id !== pending.id ||
    frame.type !== "reply" ||
    frame.ok !== true
  )
    return fatal();
  const { res, stream } = pending;
  pending = undefined;
  const data = frame.body;
  if (!stream) {
    res
      .writeHead(200, { "Content-Type": "application/json" })
      .end(JSON.stringify(data));
    return;
  }
  const choice = data.choices[0],
    delta = { ...choice.message };
  if (delta.tool_calls)
    delta.tool_calls = delta.tool_calls.map((t, index) => ({ ...t, index }));
  res.writeHead(200, { "Content-Type": "text/event-stream" });
  const base = {
    id: data.id,
    object: "chat.completion.chunk",
    created: data.created,
    model: data.model,
  };
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
  const sha = /^[a-f0-9]{64}$/,
    uuid = /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
  if (
    initialized ||
    Object.keys(frame).sort().join() !==
      "context,contextTokens,format,inputPrice,model,output,outputPrice,prompt,type" ||
    frame.type !== "init" ||
    !["md", "txt"].includes(frame.format) ||
    typeof frame.prompt !== "string" ||
    !frame.prompt.trim() ||
    Buffer.byteLength(frame.prompt) > 256 * 1024 ||
    typeof frame.model !== "string" ||
    !/^[a-z0-9][a-z0-9._-]{0,80}$/.test(frame.model) ||
    !Number.isSafeInteger(frame.output) ||
    frame.output < 1 ||
    frame.output > 8192 ||
    !Number.isSafeInteger(frame.contextTokens) ||
    frame.contextTokens < 1 ||
    frame.contextTokens > 200000 ||
    !Number.isFinite(frame.inputPrice) ||
    frame.inputPrice <= 0 ||
    !Number.isFinite(frame.outputPrice) ||
    frame.outputPrice <= 0
  )
    throw new Error("Invalid init");
  const ctx = frame.context;
  if (
    !ctx ||
    Object.keys(ctx).sort().join() !==
      "configuration_sha256,input_sha256,plan_sha256,request_sha256,revision,run_id" ||
    !uuid.test(ctx.run_id) ||
    !Number.isSafeInteger(ctx.revision) ||
    ctx.revision < 1 ||
    ctx.revision > 32 ||
    [
      "configuration_sha256",
      "input_sha256",
      "plan_sha256",
      "request_sha256",
    ].some((k) => typeof ctx[k] !== "string" || !sha.test(ctx[k]))
  )
    throw new Error("Invalid context");
  initialized = true;
  await mkdir("/tmp/fiber/agent", { recursive: true });
  await writeFile(
    "/tmp/fiber/creation-context.json",
    JSON.stringify({ context: ctx, format: frame.format }),
    { mode: 0o600, flag: "wx" },
  );
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  await writeFile(
    "/tmp/fiber/agent/models.json",
    JSON.stringify({
      providers: {
        "fiber-host": {
          baseUrl: `http://127.0.0.1:${server.address().port}/v1`,
          api: "openai-completions",
          apiKey: "container-placeholder",
          models: [
            {
              id: frame.model,
              name: frame.model,
              reasoning: false,
              input: ["text"],
              cost: {
                input: frame.inputPrice,
                output: frame.outputPrice,
                cacheRead: frame.inputPrice,
                cacheWrite: frame.inputPrice,
              },
              contextWindow: frame.contextTokens,
              maxTokens: frame.output,
              compat: {
                supportsStore: false,
                supportsReasoningEffort: false,
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
      "--extension",
      "/opt/fiber/creation/draft_tool.mjs",
      "--tools",
      "fiber_write_draft",
      "--provider",
      "fiber-host",
      "--model",
      frame.model,
      "--thinking",
      "off",
    ],
    {
      cwd: "/task",
      env: {
        PATH: process.env.PATH,
        HOME: "/tmp/fiber",
        PI_CODING_AGENT_DIR: "/tmp/fiber/agent",
      },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  readline.createInterface({ input: child.stdout }).on("line", (line) => {
    try {
      emit({ type: "event", event: JSON.parse(line).type });
    } catch {
      fatal();
    }
  });
  child.stderr.on("data", () => {});
  child.on("error", fatal);
  child.on("exit", (code) => {
    emit({ type: "exit", code });
    server.close();
    process.exit(code || 0);
  });
  child.stdin.write(
    JSON.stringify({ type: "prompt", id: "task", message: frame.prompt }) +
      "\n",
  );
}
let input = Promise.resolve();
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  if (Buffer.byteLength(line) > 1024 * 1024) return fatal();
  input = input
    .then(async () => {
      const frame = JSON.parse(line);
      if (frame.type === "init") await init(frame);
      else if (frame.type === "reply") reply(frame);
      else if (frame.type === "abort") child?.stdin.write('{"type":"abort"}\n');
      else fatal();
    })
    .catch(fatal);
});
process.stdin.on("end", () => {
  child?.kill();
  server.close();
  process.exit(0);
});
