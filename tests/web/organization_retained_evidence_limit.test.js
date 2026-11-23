import test from "node:test";
import assert from "node:assert/strict";
import { installDOM } from "./reliability_dom_fixture.js";

globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { setLocale } = await import("../../src/fiber_bundle/web/i18n/index.js");
const { appendExecutionStatus } =
  await import("../../src/fiber_bundle/web/organization_execution_status.js");
const { readObservationDiagnostic } =
  await import("../../src/fiber_bundle/web/organization_diagnostics.js");
const { createAdminApi } = await import("../../src/fiber_bundle/web/api.js");
const { createSession } = await import("../../src/fiber_bundle/web/session.js");
const { errorText } =
  await import("../../src/fiber_bundle/web/atlas/feedback.js");

const code = "moc_retained_member_limit";
const diagnostic = {
  reason: "retained_evidence_limit",
  phase: "moc_retained_members",
  path: "Map/热管理 MOC.md",
  moc_id: "11111111-1111-4111-8111-111111111111",
  actual_bytes: 26001,
  limit_bytes: 24576,
  missing_members: ["Inbox/缺失甲.md", "Resource/缺失乙.md"],
  changed_fields: [],
  retryable_read: false,
};

function assertCause(value, language) {
  assert.match(
    value,
    language === "en"
      ? /retaining historical MOC memberships exceeds the processing limit/
      : /MOC 历史成员保留校验证据超过可处理上限/,
  );
  assert.match(
    value,
    language === "en" ? /Existing memberships are retained/ : /已有归属保留/,
  );
  assert.doesNotMatch(value, /后台资源暂被占用|文件暂不可用|generic fallback/);
}

function assertFacts(value, language) {
  for (const path of [diagnostic.path, ...diagnostic.missing_members])
    assert(value.includes(path));
  assert(value.includes(diagnostic.moc_id));
  assert.match(
    value,
    language === "en"
      ? /26001 bytes · Limit 24576 bytes/
      : /26001 字节 · 上限 24576 字节/,
  );
  assert.match(value, language === "en" ? /Missing member:/ : /缺失成员：/);
}

test("retained-member capacity diagnostics show the actual MOC, byte limit and missing members in both languages", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const host = dom.document.createElement("section");
      appendExecutionStatus(
        host,
        { state: "partial", error: code, error_detail: diagnostic, items: [] },
        () =>
          assert.fail("known capacity failure must not use a generic fallback"),
      );
      assertCause(host.textContent, language);
      assertFacts(host.textContent, language);
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("historical capacity errors without detail describe the cause without inventing MOC, members or byte counts", () => {
  const dom = installDOM();
  try {
    for (const language of ["zh-CN", "en"]) {
      setLocale(language);
      const host = dom.document.createElement("section");
      appendExecutionStatus(
        host,
        { state: "partial", error: code, items: [] },
        () => "generic fallback",
      );
      assertCause(host.textContent, language);
      assert.doesNotMatch(host.textContent, /Map\/|Inbox\/|24576|MOC ID/);
      assert.doesNotMatch(
        host.textContent,
        /缺失成员：|Missing member:|校验证据：|Verification evidence:/,
      );
    }
  } finally {
    setLocale("zh-CN");
    dom.restore();
  }
});

test("HTTP capacity errors keep bounded evidence through the API and Atlas known-code translation without retry", async () => {
  for (const language of ["zh-CN", "en"]) {
    setLocale(language);
    let calls = 0;
    const api = createAdminApi(
      createSession(),
      { onConnection() {}, onUnauthorized() {} },
      async () => {
        calls++;
        return {
          ok: false,
          status: 503,
          json: async () => ({ error: code, error_detail: diagnostic }),
        };
      },
    );
    await assert.rejects(
      api.organization.maintenanceAction("original", "resume", {}),
      (error) => {
        assert.equal(error.code, code);
        assert.equal(error.status, 503);
        assert.deepEqual(error.diagnostic, diagnostic);
        assertCause(error.message, language);
        assertFacts(error.message, language);
        // Atlas translates recognized codes again; its detail must keep evidence.
        const notice = errorText(error);
        assertCause(notice.title, language);
        assertFacts(notice.detail, language);
        return true;
      },
    );
    assert.equal(calls, 1);
  }
  setLocale("zh-CN");
});

test("capacity evidence parsing excludes source proof text and rejects invalid metadata", () => {
  const valid = readObservationDiagnostic({
    ...diagnostic,
    missing_members: Array.from({ length: 25 }, (_, i) => `Inbox/${i}.md`),
    block: "PRIVATE SOURCE TEXT",
    registration: { secret: "NOT PUBLIC" },
  });
  assert.equal(valid.missing_members.length, 20);
  assert.equal(valid.block, undefined);
  assert.equal(valid.registration, undefined);
  const invalid = readObservationDiagnostic({
    ...diagnostic,
    moc_id: "not-a-moc-id",
    actual_bytes: Number.MAX_SAFE_INTEGER + 1,
    limit_bytes: -1,
    missing_members: ["/outside/secret", "../outside", "Inbox/valid.md"],
  });
  assert.equal(invalid.moc_id, undefined);
  assert.equal(invalid.actual_bytes, undefined);
  assert.equal(invalid.limit_bytes, undefined);
  assert.deepEqual(invalid.missing_members, ["Inbox/valid.md"]);
});
