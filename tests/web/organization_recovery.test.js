import test from "node:test";
import assert from "node:assert/strict";
globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { setLocale } = await import("../../src/fiber_bundle/web/i18n/index.js");
const {
  appendCatalogFailure,
  appendRetainedMembers,
  catalogIssueMessage,
  catalogPreflightFailed,
} = await import("../../src/fiber_bundle/web/organization_catalog_feedback.js");
const { classificationActions } =
  await import("../../src/fiber_bundle/web/organization_task_controls.js");

test("catalog preflight failure identifies a root directory without file download controls or a fake task failure", () => {
  setLocale("zh-CN");
  const previous = globalThis.document;
  globalThis.document = {
    createElement: (tag) => ({ tagName: tag, textContent: "" }),
  };
  try {
    const children = [],
      host = { append: (...items) => children.push(...items) };
    assert(
      appendCatalogFailure(host, {
        code: "organization_catalog_changed",
        issues: [
          { path: "", reason: "organization_directory_metadata_changed" },
        ],
      }),
    );
    assert.match(children[0].textContent, /预检未通过.*尚未创建/);
    assert.match(children[1].textContent, /知识库根目录/);
    assert.match(children[2].textContent, /目录观察.*更新索引/);
    assert(children.every((child) => child.tagName === "p"));
    assert.equal(
      catalogPreflightFailed({ code: "organization_model_cache_incomplete" }),
      false,
    );
  } finally {
    globalThis.document = previous;
  }
});

test("directory feedback distinguishes saved metadata from a changing read and keeps source paths raw", () => {
  setLocale("en");
  assert.match(
    String(
      catalogIssueMessage({
        reason: "organization_directory_metadata_changed",
      }),
    ),
    /observation differs/,
  );
  assert.match(
    String(
      catalogIssueMessage({
        reason: "organization_directory_changed_during_read",
      }),
    ),
    /still changing/,
  );
  assert.match(
    String(
      catalogIssueMessage({ reason: "organization_directory_names_changed" }),
    ),
    /entries changed/,
  );
  setLocale("zh-CN");
});

test("failed writeback and index recovery expose one accurately labelled resume action", () => {
  setLocale("zh-CN");
  const job = {
    state: "partial",
    index_state: "failed",
    resume_mode: "cached_writeback",
    items: [{ state: "failed", evaluated: true }],
  };
  const actions = classificationActions(job);
  assert.equal(actions.length, 1);
  assert.equal(actions[0].operation, "resume");
  assert.match(String(actions[0].label), /复用缓存.*不调用模型/);
  assert.equal(
    classificationActions({ state: "completed", index_state: "failed" })[0]
      .label,
    "重试索引更新",
  );
  assert.deepEqual(
    classificationActions({ state: "completed", index_state: "ready" }),
    [],
  );
  setLocale("en");
  assert.match(String(classificationActions(job)[0].label), /no model calls/);
  setLocale("zh-CN");
});

test("retained historical memberships remain visibly pending review without claiming file recovery", () => {
  setLocale("zh-CN");
  const previous = globalThis.document;
  globalThis.document = {
    createElement: (tag) => ({ tagName: tag, textContent: "" }),
  };
  try {
    const children = [],
      host = { append: (...items) => children.push(...items) };
    assert.equal(appendRetainedMembers(host, []), false);
    assert.equal(
      appendRetainedMembers(host, [
        { identity: "note:old", path: "Resource/旧笔记.md" },
      ]),
      true,
    );
    assert.match(children[0].textContent, /已保留原 MOC 关系.*仍需核对/);
    assert.equal(children[1].textContent, "Resource/旧笔记.md");
    assert(children.every((child) => child.tagName === "p"));
  } finally {
    globalThis.document = previous;
  }
});
