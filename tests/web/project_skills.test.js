import test from "node:test";
import assert from "node:assert/strict";
import { createAdminApi } from "../../src/fiber_bundle/web/api.js";
import { previewStatus } from "../../src/fiber_bundle/web/source_import_preview.js";

test("project Skill selection uses the authenticated CAS endpoint", async () => {
  const calls = [],
    api = createAdminApi(
      { revision: 1, csrf: "synthetic" },
      {
        onConnection() {},
        onUnauthorized() {},
      },
      async (url, options) => {
        calls.push({ url, options });
        return { ok: true, json: async () => ({}) };
      },
    );
  await api.projectSkills();
  const value = {
    enabled: true,
    image: "sha256:" + "a".repeat(64),
    expected_sha256: null,
  };
  await api.saveProjectSkills(value);
  assert.equal(calls[0].url, "/admin/project-skills");
  assert.equal(calls[1].options.method, "PUT");
  assert.equal(calls[1].options.headers["X-CSRF-Token"], "synthetic");
  assert.deepEqual(JSON.parse(calls[1].options.body), value);
});

test("project native PDF preview reflects selection without changing ordinary or scan routes", () => {
  const project = { settings: { enabled: true } };
  assert.match(
    previewStatus("pdf", {}, "fake-expert", project),
    /项目 fake-expert/,
  );
  assert.match(previewStatus("txt", {}, "fake-expert", project), /未就绪/);
  assert.equal(
    previewStatus("pdf", {}, "ordinary", project),
    previewStatus("pdf", {}),
  );
});
