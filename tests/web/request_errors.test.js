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

test("a blocked preview submission tells the user no task was created without repeating the POST", async () => {
  for (const [code, status] of [
    ["maintenance_submission_not_started", 503],
    ["maintenance_submission_changed", 409],
  ]) {
    const requests = [];
    const api = createAdminApi(
      createSession(),
      { onConnection() {}, onUnauthorized() {} },
      async (path, options) => {
        requests.push([path, options.method]);
        return {
          ok: false,
          status,
          json: async () => ({ error: code, task_created: false }),
        };
      },
    );
    await assert.rejects(
      api.organization.maintain({ mode: "quick" }),
      (error) => {
        assert.equal(error.code, code);
        for (const [locale, pattern] of [
          ["zh-CN", /未创建/],
          ["en", /No .*task was created/],
        ]) {
          setLocale(locale);
          assert.match(errorText(error).title, pattern);
        }
        setLocale("zh-CN");
        return true;
      },
    );
    assert.deepEqual(requests, [["/admin/organization/maintenance", "POST"]]);
  }
});

test("a recorded submission with an unavailable result keeps the task ID for a read-only refresh", async () => {
  const code = "maintenance_submission_result_unavailable";
  const taskId = "5e62e433-f3da-48a3-a1ac-4be952829261";
  let calls = 0;
  const api = createAdminApi(
    createSession(),
    { onConnection() {}, onUnauthorized() {} },
    async () => {
      calls++;
      return {
        ok: false,
        status: 503,
        json: async () => ({ error: code, task_id: taskId }),
      };
    },
  );
  await assert.rejects(
    api.organization.maintain({ mode: "quick" }),
    (error) => {
      assert.equal(error.taskId, taskId);
      assert.match(error.message, /已登记/);
      return true;
    },
  );
  assert.equal(calls, 1);
});

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

for (const code of [
  "maintenance_membership_plan_changed",
  "maintenance_membership_baseline_changed",
]) {
  test(`${code}: resume errors keep their code and show actionable bilingual toast text`, async () => {
    let calls = 0;
    const api = createAdminApi(
      createSession(),
      { onConnection() {}, onUnauthorized() {} },
      async () => {
        calls++;
        return { ok: false, status: 409, json: async () => ({ error: code }) };
      },
    );
    await assert.rejects(
      api.organization.maintenanceAction("task", "resume", {}),
      (error) => {
        assert.equal(error.code, code);
        for (const locale of ["zh-CN", "en"]) {
          setLocale(locale);
          assert.deepEqual(errorText(error), {
            title: requestErrors[code][locale === "en" ? 1 : 0],
            detail: code,
          });
        }
        setLocale("zh-CN");
        return true;
      },
    );
    assert.equal(calls, 1);
  });
}
