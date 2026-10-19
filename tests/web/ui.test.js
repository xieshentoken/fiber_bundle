import test from "node:test";
import assert from "node:assert/strict";
import {
  action,
  finishLoading,
  setBusy,
  showLoading,
} from "../../src/fiber_bundle/web/ui.js";

function buttonControl(disabled = false) {
  return {
    disabled,
    attributes: new Map(),
    setAttribute(name, value) {
      this.attributes.set(name, value);
    },
    removeAttribute(name) {
      this.attributes.delete(name);
    },
    addEventListener(name, work) {
      this[name] = work;
    },
  };
}

test("busy presentation preserves policy-disabled state", () => {
  const button = buttonControl(true);
  setBusy(button, true);
  assert.equal(button.attributes.get("aria-busy"), "true");
  assert.equal(button.disabled, true);
  setBusy(button, false);
  assert.equal(button.attributes.has("aria-busy"), false);
  assert.equal(button.disabled, true);
});

test("an action is busy through completion and recovers after a failure", async () => {
  for (const failure of [false, true]) {
    const button = buttonControl();
    let finish;
    const work = new Promise((resolve, reject) => {
      finish = () =>
        failure ? reject(new Error("synthetic failure")) : resolve();
    });
    const previousDocument = globalThis.document;
    const notice = {};
    globalThis.document = { getElementById: () => notice };
    try {
      action(button, () => work);
      const pending = button.click();
      assert.equal(button.disabled, true);
      assert.equal(button.attributes.get("aria-busy"), "true");
      finish();
      await pending;
      assert.equal(button.disabled, false);
      assert.equal(button.attributes.has("aria-busy"), false);
      if (failure) assert.equal(notice.textContent, "synthetic failure");
    } finally {
      globalThis.document = previousDocument;
    }
  }
});

test("loading feedback marks a list busy and clears it after rendering", () => {
  const attributes = new Map();
  const container = {
    children: [],
    setAttribute(name, value) {
      attributes.set(name, value);
    },
    removeAttribute(name) {
      attributes.delete(name);
    },
    replaceChildren(...children) {
      this.children = children;
    },
  };
  const previousDocument = globalThis.document;
  globalThis.document = {
    getElementById: () => container,
    createElement: (tagName) => ({
      tagName,
      remove() {
        container.children = container.children.filter(
          (child) => child !== this,
        );
      },
      setAttribute(name, value) {
        this[name] = value;
      },
    }),
  };
  try {
    showLoading("vault-list");
    assert.equal(attributes.get("aria-busy"), "true");
    assert.equal(container.children.length, 2);
    assert.equal(container.children[0]["aria-hidden"], "true");
    finishLoading("vault-list");
    assert.equal(attributes.has("aria-busy"), false);
    assert.equal(container.children.length, 0);
  } finally {
    globalThis.document = previousDocument;
  }
});

test("a completed stop action retains its newly disabled policy", async () => {
  const button = buttonControl();
  button.dataset = {};
  action(button, async () => {
    button.dataset.policyDisabled = "true";
  });
  const previous = globalThis.document;
  globalThis.document = { getElementById: () => ({ dataset: {} }) };
  try {
    await button.click();
    assert.equal(button.disabled, true);
    assert.equal(button.attributes.has("aria-busy"), false);
  } finally {
    globalThis.document = previous;
  }
});

test("native modal completion restores lost focus only to an available visible trigger", async () => {
  const previous = globalThis.document;
  try {
    for (const state of [
      "body",
      "editing",
      "hidden",
      "removed",
      "disabled",
      "default",
    ]) {
      const button = buttonControl();
      const body = {};
      const input = {};
      button.isConnected = state !== "removed";
      button.closest = (selector) =>
        selector === "[hidden]" && state === "hidden" ? { hidden: true } : null;
      button.dataset = { policyDisabled: String(state === "disabled") };
      let focused = 0;
      button.focus = () => {
        focused++;
      };
      globalThis.document = {
        body,
        activeElement: state === "editing" ? input : body,
        getElementById: () => ({ dataset: {} }),
      };
      action(button, async () => {}, { restoreFocus: state !== "default" });
      await button.click();
      assert.equal(focused, state === "body" ? 1 : 0, state);
    }
  } finally {
    globalThis.document = previous;
  }
});

test("page feedback is scoped and cannot reappear after navigating away and back", async () => {
  const { message, captureMessage, setFeedbackPage } =
    await import("../../src/fiber_bundle/web/ui.js");
  const previous = globalThis.document,
    notice = { dataset: {} };
  globalThis.document = { getElementById: () => notice };
  try {
    setFeedbackPage("models");
    const old = captureMessage("models");
    old("模型配置已载入");
    assert.equal(notice.hidden, false);
    setFeedbackPage("backups");
    assert.equal(notice.hidden, true);
    message("无关成功", false, "models");
    assert.equal(notice.hidden, true);
    setFeedbackPage("models");
    old("迟到成功");
    assert.equal(notice.hidden, true);
    message("连接失败", true);
    setFeedbackPage("backups");
    assert.equal(notice.textContent, "连接失败");
    assert.equal(notice.hidden, false);
    const signedIn = captureMessage("");
    setFeedbackPage(null);
    signedIn("旧会话错误");
    assert.equal(notice.hidden, true);
  } finally {
    globalThis.document = previous;
  }
});

test("slow refresh feedback preserves existing editable list nodes", () => {
  const draft = { value: "unsaved question" };
  const container = {
    children: [draft],
    setAttribute() {},
    replaceChildren() {
      assert.fail("a refresh must not remove existing draft nodes");
    },
  };
  const previous = globalThis.document;
  globalThis.document = { getElementById: () => container };
  try {
    showLoading("batch-list");
    assert.equal(container.children[0], draft);
    assert.equal(draft.value, "unsaved question");
  } finally {
    globalThis.document = previous;
  }
});

test("loading completion removes placeholders and keeps real content", () => {
  const placeholder = {
      className: "skeleton",
      remove() {
        this.removed = true;
      },
    },
    content = {
      className: "row",
      remove() {
        assert.fail("real content must survive");
      },
    },
    previous = globalThis.document;
  globalThis.document = {
    getElementById: () => ({
      children: [placeholder, content],
      removeAttribute() {},
    }),
  };
  try {
    finishLoading("vault-list");
    assert.equal(placeholder.removed, true);
  } finally {
    globalThis.document = previous;
  }
});
