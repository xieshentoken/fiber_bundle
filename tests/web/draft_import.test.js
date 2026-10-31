import test from "node:test";
import assert from "node:assert/strict";
import { createDraftImportApi } from "../../src/fiber_bundle/web/draft_import_api.js";
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
const { canConfirmDraft, importScopeAllowed } =
  await import("../../src/fiber_bundle/web/draft_import_ui.js");
const { taskSummary, draftError } =
  await import("../../src/fiber_bundle/web/draft_import_labels.js");
const { setLocale } = await import("../../src/fiber_bundle/web/i18n/index.js");

test("import scopes require active query+add and a writable active vault", () => {
  const vault = { id: "v", status: "active", read_only: false };
  const client = {
    active: true,
    grants: [{ vault_id: "v", can_add: true, can_query: true }],
  };
  assert.equal(importScopeAllowed(client, vault), true);
  assert.equal(
    importScopeAllowed(client, { ...vault, read_only: true }),
    false,
  );
  assert.equal(importScopeAllowed({ ...client, active: false }, vault), false);
  assert.equal(
    importScopeAllowed(client, { ...vault, status: "unlinked" }),
    false,
  );
  assert.equal(
    importScopeAllowed(
      {
        ...client,
        grants: [{ vault_id: "v", can_add: false, can_query: true }],
      },
      vault,
    ),
    false,
  );
});
test("only an acknowledged unconfirmed preview can import", () => {
  assert.equal(
    canConfirmDraft({ state: "preview", confirmed: false }, true),
    true,
  );
  for (const state of ["queued", "working", "failed", "completed"])
    assert.equal(canConfirmDraft({ state, confirmed: false }, true), false);
  assert.equal(
    canConfirmDraft({ state: "preview", confirmed: true }, true),
    false,
  );
  assert.equal(
    canConfirmDraft({ state: "preview", confirmed: false }, false),
    false,
  );
});
test("partial and cancelled states never claim full completion in either language", () => {
  for (const locale of ["zh-CN", "en"]) {
    setLocale(locale);
    assert.notEqual(
      taskSummary({ state: "partial" }),
      taskSummary({ state: "completed" }),
    );
    assert.notEqual(
      taskSummary({ state: "cancelled" }),
      taskSummary({ state: "completed" }),
    );
    assert.ok(draftError("draft_source_changed").length > 20);
  }
  setLocale("zh-CN");
});
test("transport preserves scope and preview and never retries mutations", async () => {
  const calls = [];
  const api = createDraftImportApi(async (...args) => {
    calls.push(args);
    return {};
  });
  const body = {
    client_id: "c",
    vault_id: "v",
    preview_sha256: "frozen",
    moc_ids: ["m"],
  };
  await api.confirm("a/b", body);
  assert.deepEqual(calls, [
    ["/admin/draft-imports/a%2Fb/confirm", "POST", body],
  ]);
  await api.action("a", "resume", { client_id: "c", vault_id: "v" });
  assert.equal(calls.length, 2);
});
