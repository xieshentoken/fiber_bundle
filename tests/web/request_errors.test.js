import test from "node:test";
import assert from "node:assert/strict";
const preferences = new Map();
globalThis.localStorage = {
  getItem: (key) => preferences.get(key) ?? null,
  setItem: (key, value) => preferences.set(key, value),
};
const { createAdminApi } = await import("../../src/fiber_bundle/web/api.js");
const { createSession } = await import("../../src/fiber_bundle/web/session.js");
const { setLocale } = await import("../../src/fiber_bundle/web/i18n/index.js");
const { errorText } =
  await import("../../src/fiber_bundle/web/atlas/feedback.js");
const { readState } = await import("../../src/fiber_bundle/web/home_status.js");
const { architecture, criticalAlert, homeStatus } =
  await import("../../src/fiber_bundle/web/home_model.js");
const { criticalAlert: atlasAlert, readText } =
  await import("../../src/fiber_bundle/web/atlas/home_model.js");
const { requestErrors } =
  await import("../../src/fiber_bundle/web/request_errors.js");

for (const code of [
  "storage_busy",
  "display_busy",
  "display_changed",
  "backup_snapshot_busy",
  "organization_progress_busy",
]) {
  test(`${code}: transient reads retain unverified state without a system/storage alarm`, () => {
    const record = {
      error: { code, status: 503, message: requestErrors[code][0] },
      value: { state: "running" },
    };
    assert.equal(readState(record).state, "unverified");
    assert(!readState(record).text.includes("存储暂不可读"));
    assert.equal(criticalAlert({ backend: record }), null);
    assert.equal(atlasAlert({ backend: record }), null);
    assert.equal(homeStatus({ backend: record }).tone, "waiting");
    assert.equal(
      architecture({ backend: record }).nodes.backend.tone,
      "waiting",
    );
    for (const locale of ["en", "zh-CN"]) {
      setLocale(locale);
      assert.equal(
        errorText(record.error).title,
        requestErrors[code][locale === "en" ? 1 : 0],
      );
      assert(
        !/存储暂不可读|Storage unreadable|Storage unavailable/.test(
          readText(record),
        ),
      );
      if (locale === "en") assert(!/[\u3400-\u9fff]/.test(readText(record)));
    }
    setLocale("zh-CN");
  });
}

test("storage faults remain errors and never retry automatically", async () => {
  for (const code of ["storage_unavailable", "display_limit_exceeded"]) {
    let calls = 0;
    const api = createAdminApi(
      createSession(),
      { onConnection() {}, onUnauthorized() {} },
      async () => {
        calls++;
        return { ok: false, status: 503, json: async () => ({ error: code }) };
      },
    );
    await assert.rejects(
      api.vaults(),
      (e) => e.code === code && e.message === requestErrors[code][0],
    );
    assert.equal(calls, 1);
  }
  const record = { error: { code: "storage_unavailable", status: 503 } };
  assert.equal(homeStatus({ backend: record }).tone, "error");
  assert(criticalAlert({ backend: record }));
  assert(atlasAlert({ backend: record }));
});
