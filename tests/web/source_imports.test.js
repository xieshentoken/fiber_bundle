import test from "node:test";
import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { webcrypto } from "node:crypto";
import { createAdminApi } from "../../src/fiber_bundle/web/api.js";
import {
  fileSha,
  encodeChunk,
} from "../../src/fiber_bundle/web/source_imports.js";

test("import operations share session CSRF and encode fixed scope and identity", async () => {
  const calls = [];
  const api = createAdminApi(
    { revision: 1, csrf: "synthetic" },
    { onConnection() {}, onUnauthorized() {} },
    async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true,
        json: async () => (url.startsWith("/admin/imports?") ? [] : {}),
      };
    },
  );
  await api.imports("client/a", "vault/b");
  await api.createImport({ action: "archive", submission_id: "explicit" });
  await api.importChunk("i/d", {
    ordinal: 1,
    sha256: "a".repeat(64),
    data: "YQ==",
  });
  await api.finishImport("i/d", "owner");
  await api.startImport("i/d", "owner");
  await api.cancelImport("i/d", "owner");
  assert.equal(
    calls[0].url,
    "/admin/imports?client_id=client%2Fa&vault_id=vault%2Fb",
  );
  assert.equal(calls[2].url, "/admin/imports/i%2Fd/chunks");
  assert.equal(calls[2].options.method, "PUT");
  for (const call of calls.slice(1))
    assert.equal(call.options.headers["X-CSRF-Token"], "synthetic");
  for (const call of calls.slice(3))
    assert.deepEqual(JSON.parse(call.options.body), { client_id: "owner" });
});

test("hashes and chunk encoding preserve every binary byte", async () => {
  globalThis.window = { crypto: webcrypto, btoa: globalThis.btoa };
  try {
    const data = new Uint8Array(256 * 1024);
    for (let i = 0; i < data.length; i++) data[i] = i % 256;
    assert.deepEqual(
      new Uint8Array(Buffer.from(encodeChunk(data), "base64")),
      data,
    );
    const hash = await fileSha(new Blob([data]));
    assert.match(hash, /^[a-f0-9]{64}$/);
    assert.equal(hash, await fileSha(new Blob([data])));
    data[0] = 1;
    assert.notEqual(hash, await fileSha(new Blob([data])));
  } finally {
    delete globalThis.window;
  }
});

test("index conditions use bounded local explanations, never server exception text", async () => {
  for (const [code, expected] of [
    ["note_index_update_required", "全文索引需要更新"],
    ["note_index_match_limit", "匹配内容过多"],
  ]) {
    const api = createAdminApi(
      { revision: 1, csrf: "synthetic" },
      { onConnection() {}, onUnauthorized() {} },
      async () => ({
        ok: false,
        status: 409,
        json: async () => ({ error: code }),
      }),
    );
    await assert.rejects(api.sourcePolicy(), (error) =>
      error.message.includes(expected),
    );
  }
});

test("full compiler preserves explicit task/workpack scope, CSRF and query text", async () => {
  const calls = [],
    api = createAdminApi(
      { revision: 1, csrf: "synthetic" },
      { onConnection() {}, onUnauthorized() {} },
      async (url, options) => {
        calls.push({ url, options });
        return { ok: true, json: async () => ({}) };
      },
    );
  await api.resumeExpert("task/id", {
    client_id: "owner",
    workpack_import_id: "pack/id",
  });
  await api.expertArtifact("task/id", "owner/id");
  await api.expertQuery("task/id", "owner/id", "功率 12 W & safety?");
  await api.publishExpert("task/id", {
    client_id: "owner",
    candidate_acknowledged: true,
  });
  assert.equal(calls[0].url, "/admin/imports/task%2Fid/resume-expert");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    client_id: "owner",
    workpack_import_id: "pack/id",
  });
  assert.equal(calls[0].options.headers["X-CSRF-Token"], "synthetic");
  assert.equal(
    calls[2].url,
    "/admin/imports/task%2Fid/expert-query?client_id=owner%2Fid&question=" +
      encodeURIComponent("功率 12 W & safety?"),
  );
  assert.equal(calls[3].options.headers["X-CSRF-Token"], "synthetic");
});
