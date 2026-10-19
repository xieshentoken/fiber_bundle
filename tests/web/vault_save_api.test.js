import test from "node:test";
import assert from "node:assert/strict";
import { createAdminApi } from "../../src/fiber_bundle/web/api.js";
import { createSession } from "../../src/fiber_bundle/web/session.js";

function fixture(reply) {
  const calls = [],
    session = createSession();
  session.accept({ csrf: "synthetic-layout-csrf", expires_at: 4102444800 });
  const api = createAdminApi(
    session,
    { onConnection: () => {}, onUnauthorized: () => {} },
    async (url, options) => {
      calls.push({ url, ...options });
      return { ok: true, json: async () => reply };
    },
  );
  return { api, calls };
}
const page = {
  snapshot: "synthetic",
  directories: [{ name: "Inbox", relative_path: "Inbox", selectable: true }],
  next_cursor: null,
  truncated: false,
};

test("directory discovery uses only directory APIs, encodes cursors and preserves authentication", async () => {
  const { api, calls } = fixture(page);
  await api.vaultSave.discover({ root: "/synthetic/vault" });
  await api.vaultSave.directories("vault/id", "资料/Inbox", "snapshot:100");
  assert.equal(calls[0].url, "/admin/vault-catalog/directory-preview");
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].headers["X-CSRF-Token"], "synthetic-layout-csrf");
  assert.equal(calls[0].credentials, "same-origin");
  assert.equal(
    calls[1].url,
    "/admin/vault-catalog/vault%2Fid/directories?relative_parent=%E8%B5%84%E6%96%99%2FInbox&cursor=snapshot%3A100",
  );
  assert.equal(calls[1].method, "GET");
  assert.equal(calls[1].body, undefined);
});

test("malformed directory and preview responses fail closed", async () => {
  await assert.rejects(
    fixture({ ...page, directories: [{}] }).api.vaultSave.discover({
      root: "/synthetic",
    }),
  );
  await assert.rejects(
    fixture({
      preview_id: "synthetic",
      paths: { note: {} },
    }).api.vaultSave.preview("id", {}),
  );
});

test("draft save uses PUT and only acknowledged revision and preview token", async () => {
  const settings = {
    layout_mode: "obsidian_folders",
    inbox_dir: "Inbox",
    archive_dir: "Archive",
    image_dir: "images",
    image_link_style: "obsidian",
    archive_on_moc: false,
    moc_destinations: {},
  };
  const { api, calls } = fixture({
    revision: 1,
    settings,
    mocs: [],
    publication_ready: false,
  });
  await api.vaultSave.save("vault", {
    expected_revision: 0,
    preview_id: "synthetic",
  });
  assert.equal(calls[0].method, "PUT");
  assert.deepEqual(JSON.parse(calls[0].body), {
    expected_revision: 0,
    preview_id: "synthetic",
  });
});

test("interrupted operation resolution always explicitly preserves directories", async () => {
  const { api, calls } = fixture({
    state: "cancelled",
    directories_preserved: true,
  });
  await api.vaultSave.abandon("vault", "operation");
  assert.equal(calls[0].url, "/admin/vault-catalog/vault/save-policy/abandon");
  assert.deepEqual(JSON.parse(calls[0].body), {
    operation_id: "operation",
    preserve_directories: true,
  });
});

test("known bounded save errors are translated without echoing provider content", async () => {
  const session = createSession();
  session.accept({ csrf: "synthetic", expires_at: 4102444800 });
  const api = createAdminApi(
    session,
    { onConnection: () => {}, onUnauthorized: () => {} },
    async () => ({
      ok: false,
      status: 409,
      json: async () => ({
        error: "save_policy_revision_conflict",
        detail: "DO NOT DISPLAY",
      }),
    }),
  );
  await assert.rejects(
    api.vaultSave.preview("id", {}),
    (error) =>
      error.status === 409 &&
      error.message.includes("保存规则已被修改") &&
      !error.message.includes("DO NOT DISPLAY"),
  );
});

test("version-two settings require all roles and an explicit archive mode", async () => {
  const value = {
    revision: 2,
    publication_ready: true,
    mocs: [],
    settings: {
      policy_version: 2,
      layout_mode: "obsidian_folders",
      inbox_dir: "Inbox",
      archive_dir: "Resource",
      image_dir: "Attachments/images",
      image_link_style: "obsidian",
      archive_on_moc: false,
      moc_destinations: {},
      moc_dir: "Map",
      book_dir: "Book",
      attachment_root: "Attachments",
      original_dir: "Attachments/pristin",
      moc_archive_mode: "off",
    },
  };
  await fixture(value).api.vaultSave.get("vault");
  for (const [key, invalid] of [
    ["policy_version", 3],
    ["book_dir", null],
    ["moc_archive_mode", "yes"],
    ["archive_on_moc", true],
  ])
    await assert.rejects(
      fixture({
        ...value,
        settings: { ...value.settings, [key]: invalid },
      }).api.vaultSave.get("vault"),
    );
});

test("automatic queue accepts arrays and rejects malformed records", async () => {
  const { api, calls } = fixture([]);
  assert.deepEqual(await api.vaultSave.automaticJobs("vault"), []);
  assert.equal(calls[0].url, "/admin/vault-catalog/vault/automatic-jobs");
  await assert.rejects(fixture({}).api.vaultSave.automaticJobs("vault"));
  await assert.rejects(fixture([{}]).api.vaultSave.automaticJobs("vault"));
});

test("attachment projections validate list responses without rejecting empty queues", async () => {
  assert.deepEqual(
    await fixture([]).api.vaultSave.attachmentProjections("vault"),
    [],
  );
  const items = [
    {
      id: "copy",
      state: "complete",
      original_name: "source.pdf",
      path: "Attachments/source.pdf",
    },
  ];
  assert.deepEqual(
    await fixture(items).api.vaultSave.attachmentProjections("vault"),
    items,
  );
  await assert.rejects(
    fixture({}).api.vaultSave.attachmentProjections("vault"),
  );
  await assert.rejects(
    fixture([{ ...items[0], path: null }]).api.vaultSave.attachmentProjections(
      "vault",
    ),
  );
});
