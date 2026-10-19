import test from "node:test";
import assert from "node:assert/strict";
import {
  rememberDraft,
  editDraft,
  hasUnsavedChanges,
  captureDraftSave,
  clearDrafts,
} from "../../src/fiber_bundle/web/unsaved_changes.js";
function field(value = "", type = "text") {
  return {
    type,
    value,
    checked: false,
    dataset: { draft: "tracked" },
    matches: () => true,
  };
}
function root(...nodes) {
  return { querySelectorAll: () => nodes };
}

test("browsing selectors and ignored controls do not create drafts", () => {
  for (const draft of [undefined, "ignore"]) {
    const input = field("a");
    input.dataset.draft = draft;
    rememberDraft(root(input));
    input.value = "b";
    editDraft(input);
    assert.equal(hasUnsavedChanges(root(input)), false);
  }
});

test("pending save updates baseline even when the user returned to the old value", () => {
  const input = field("a"),
    other = field("untouched");
  rememberDraft(root(input, other));
  input.value = "b";
  editDraft(input);
  const acknowledge = captureDraftSave(root(input));
  input.value = "a";
  editDraft(input);
  other.value = "separate draft";
  editDraft(other);
  acknowledge();
  assert.equal(hasUnsavedChanges(root(input)), true);
  input.value = "b";
  editDraft(input);
  assert.equal(hasUnsavedChanges(root(input)), false);
  assert.equal(hasUnsavedChanges(root(other)), true);
});

test("only user edits count as drafts; undoing an edit removes the warning", () => {
  const input = field("saved"),
    panel = root(input);
  rememberDraft(panel);
  input.value = "server update";
  assert.equal(hasUnsavedChanges(panel), false);
  rememberDraft(panel);
  input.value = "new draft";
  editDraft(input);
  assert.equal(hasUnsavedChanges(panel), true);
  input.value = "server update";
  editDraft(input);
  assert.equal(hasUnsavedChanges(panel), false);
});

test("a successful save does not acknowledge edits made while the request was pending", () => {
  const input = field("original"),
    panel = root(input);
  rememberDraft(panel);
  input.value = "submitted";
  editDraft(input);
  const acknowledge = captureDraftSave(panel);
  input.value = "newer edit";
  acknowledge();
  assert.equal(hasUnsavedChanges(panel), true);
  input.value = "submitted";
  assert.equal(hasUnsavedChanges(panel), false);
});

test("draft tracking excludes passwords, files and readonly credentials", () => {
  const password = field("synthetic-password", "password"),
    file = field("synthetic-file", "file"),
    token = { ...field("synthetic-token"), readOnly: true };
  const panel = root(password, file, token);
  rememberDraft(panel);
  for (const input of [password, file, token]) {
    editDraft(input);
    input.value = "different";
  }
  assert.equal(hasUnsavedChanges(panel), false);
});

test("checkbox drafts compare choices, and session clearing removes all tracked drafts", () => {
  const input = field("", "checkbox"),
    panel = root(input);
  rememberDraft(panel);
  input.checked = true;
  editDraft(input);
  assert.equal(hasUnsavedChanges(panel), true);
  captureDraftSave(panel)();
  assert.equal(hasUnsavedChanges(panel), false);
  input.checked = false;
  editDraft(input);
  assert.equal(hasUnsavedChanges(panel), true);
  clearDrafts();
  assert.equal(hasUnsavedChanges(panel), false);
});

test("leave confirmation preserves drafts when cancelled and forced session clear bypasses it", async () => {
  const { createDraftGuard } =
    await import("../../src/fiber_bundle/web/unsaved_changes.js");
  const previous = globalThis.window,
    events = {};
  const input = field(""),
    panel = root(input),
    workspace = {
      ...root(input),
      addEventListener() {},
      querySelector: () => panel,
      querySelectorAll: (selector) => (selector === "form" ? [] : [input]),
    };
  globalThis.window = {
    addEventListener(name, work) {
      events[name] = work;
    },
    confirm: () => false,
  };
  try {
    const guard = createDraftGuard(workspace, {
      extraChanges: () => false,
      signedIn: () => true,
    });
    input.value = "draft";
    editDraft(input);
    assert.equal(guard.confirmLeave(), false);
    assert.equal(input.value, "draft");
    const unload = {
      preventDefault() {
        this.prevented = true;
      },
    };
    events.beforeunload(unload);
    assert.equal(unload.prevented, true);
    guard.clear();
    assert.equal(hasUnsavedChanges(panel), false);
  } finally {
    globalThis.window = previous;
  }
});
