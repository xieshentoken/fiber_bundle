import test from "node:test";
import assert from "node:assert/strict";
import { createSupplierApi } from "../../src/fiber_bundle/web/suppliers_api.js";
import { allowedDownload } from "../../desktop/policy.mjs";

test("supplier transport sends explicit scope and commands without implicit retries", async () => {
  const calls = [];
  const api = createSupplierApi(async (...args) => {
    calls.push(args);
    return {};
  });
  const body = {
    client_id: "reader",
    vault_id: "supplier",
    request_id: "unique",
  };
  await api.preview(body);
  await api.importLegacy({ ...body, expected_sha256: "a".repeat(64) });
  await api.manual(body);
  await api.query(body);
  await api.compare(body);
  await api.mocs(body);
  assert.deepEqual(
    calls.map((c) => c.slice(0, 2)),
    [
      "legacy/preview",
      "legacy/import",
      "manual",
      "query",
      "compare",
      "classification-preview",
    ].map((p) => ["/admin/suppliers/" + p, "POST"]),
  );
  assert(
    calls.every(
      (c) => c[2].client_id === "reader" && c[2].vault_id === "supplier",
    ),
  );
});
test("supplier original downloads retain narrow same-origin and UUID boundaries", () => {
  const id = "12345678-1234-1234-1234-123456789abc",
    root = "http://127.0.0.1:8765/";
  const url =
    root +
    `admin/suppliers/original/source-${"a".repeat(24)}?client_id=${id}&vault_id=${id}`;
  assert(allowedDownload(url, root));
  assert(allowedDownload(url + `&asset_id=${id}`, root));
  for (const bad of [
    url + "&extra=1",
    url + `&vault_id=${id}`,
    url.replace(id, "../../../private"),
    url.replace("127.0.0.1", "example.com"),
    url + "#secret",
    url + "&asset_id=arbitrary",
  ])
    assert.equal(allowedDownload(bad, root), false);
});
