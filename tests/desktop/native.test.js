import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { trusted, openNote, trashIntent } from "../../desktop/native.mjs";
const id = "11111111-1111-4111-8111-111111111111",
  backend = "http://127.0.0.1:18786/";
function windowFor(fetch) {
  return {
    webContents: {
      mainFrame: { url: backend },
      session: {
        cookies: { get: async () => [{ name: "session", value: "synthetic" }] },
        fetch,
      },
    },
  };
}
test("native requests only accept the current main frame", () => {
  const w = windowFor();
  trusted(
    { sender: w.webContents, senderFrame: w.webContents.mainFrame },
    w,
    backend,
  );
  assert.throws(() =>
    trusted(
      { sender: w.webContents, senderFrame: { url: backend } },
      w,
      backend,
    ),
  );
  assert.throws(() =>
    trusted(
      { sender: w.webContents, senderFrame: w.webContents.mainFrame },
      w,
      backend + "foreign",
    ),
  );
});
test("note open uses only backend-authorized current identity and hash, then rechecks permission", async () => {
  const dir = await realpath(await mkdtemp(join(tmpdir(), "fiber-native-"))),
    path = join(dir, "note.md");
  await writeFile(path, "# 合成笔记");
  const sha = createHash("sha256").update("# 合成笔记").digest("hex"),
    value = { clientId: id, vaultId: id, noteId: "note:" + id };
  let calls = 0,
    opened = [];
  const w = windowFor(async (url, options) => {
    assert.match(url, /\/admin\/knowledge-graph\/node\?/);
    assert.equal(options.redirect, "error");
    assert.equal(options.headers.Cookie, "session=synthetic");
    calls++;
    return {
      ok: true,
      json: async () => ({ absolute_path: path, sha256: sha }),
    };
  });
  try {
    assert.deepEqual(
      await openNote(w, backend, value, {
        openPath: async (p) => {
          opened.push(p);
          return "";
        },
      }),
      { opened: true },
    );
    assert.equal(calls, 2);
    assert.deepEqual(opened, [path]);
    await assert.rejects(
      openNote(
        w,
        backend,
        { ...value, path },
        { openPath: () => assert.fail() },
      ),
    );
    w.webContents.session.fetch = async () => ({ ok: false });
    await assert.rejects(
      openNote(w, backend, value, { openPath: () => assert.fail() }),
    );
  } finally {
    await rm(dir, { recursive: true });
  }
});
test("Trash bridge sends an intent and confirmed root through authenticated CSRF transport", async () => {
  const w = windowFor(async (url, options) => {
    assert.equal(url, backend + "admin/vault-catalog/trash/" + id + "/confirm");
    assert.equal(options.headers.Origin, "http://127.0.0.1:18786");
    assert.equal(options.headers["X-CSRF-Token"], "synthetic-csrf");
    assert.deepEqual(JSON.parse(options.body), {
      confirmed_root: "/private/tmp/reviewed",
    });
    return { ok: true, json: async () => ({ state: "trashed" }) };
  });
  assert.deepEqual(
    await trashIntent(w, backend, {
      id,
      confirmedRoot: "/private/tmp/reviewed",
      csrf: "synthetic-csrf",
    }),
    { state: "trashed" },
  );
  await assert.rejects(
    trashIntent(w, backend, { path: "/private/tmp/reviewed" }),
  );
});

test("Trash preserves controlled 401 403 422 and 503 diagnostics without IPC exception text", async () => {
 for (const status of [401,403,422,503]) {
  const w=windowFor(async()=>({ok:false,status,json:async()=>({error:"vault_trash_helper_required",detail:"synthetic-secret-must-not-leak"})}));
  const result=await trashIntent(w,backend,{id,confirmedRoot:"/private/tmp/reviewed",csrf:"synthetic-csrf"});
  assert.deepEqual(result,{native_error:{status,code:"vault_trash_helper_required"}});
  assert(!JSON.stringify(result).includes("synthetic-secret"));
 }
});
