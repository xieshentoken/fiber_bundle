import test from "node:test";
import assert from "node:assert/strict";
import { create } from "../../src/fiber_bundle/web/atlas/vault_index.js";

class Element {
  constructor(tagName) {
    Object.assign(this, {
      tagName,
      children: [],
      dataset: {},
      attributes: {},
      listeners: {},
      className: "",
      value: "",
      disabled: false,
    });
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.children = children;
    if (this.tagName === "select") this.value = children[0]?.value || "";
  }
  setAttribute(key, value) {
    this.attributes[key] = value;
  }
  addEventListener(key, handler) {
    this.listeners[key] = handler;
  }
  get textContent() {
    return this.children
      .map((child) =>
        child instanceof Element ? child.textContent : String(child),
      )
      .join(" ");
  }
  set textContent(value) {
    this.children = [value];
  }
}

function environment(t) {
  t.mock.method(Date, "now", () => 1800000000000);
  for (const [key, value] of Object.entries({
    Node: Element,
    document: {
      createElement: (tag) => new Element(tag),
      createElementNS: (_, tag) => new Element(tag),
    },
  })) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, {
      value,
      configurable: true,
      writable: true,
    });
    t.after(() =>
      previous
        ? Object.defineProperty(globalThis, key, previous)
        : delete globalThis[key],
    );
  }
}

function fixture(t) {
  environment(t);
  let error = null,
    reads = 0;
  const view = create(
    {
      api: {
        async noteIndexStatus() {
          reads++;
          if (error) throw error;
          return { state: "ready", notes: 6789, built_at: 1700000000 };
        },
      },
      store: {
        async clients() {
          return [
            {
              id: "reader",
              name: "Reader",
              active: true,
              grants: [{ vault_id: "vault", can_query: true }],
            },
          ];
        },
      },
    },
    { id: "vault", status: "active" },
  );
  t.after(() => view.dispose());
  return {
    view,
    fail(value) {
      error = value;
    },
    reads: () => reads,
  };
}

test("an index refresh keeps its last successful snapshot and time on a transient read failure", async (t) => {
  const { view, fail, reads } = fixture(t);
  await view.refresh();
  const successful = view.node.textContent;
  assert.match(successful, /6789/);
  assert.match(successful, /上次成功读取状态/);
  const checked = new Date(Date.now()).toLocaleString();
  assert.ok(successful.includes(checked));
  fail({ status: 503, code: "display_changed" });
  t.mock.method(Date, "now", () => 1800086400000);
  await view.refresh();
  assert.match(view.node.textContent, /6789/);
  assert.match(view.node.textContent, /以下保留上次结果/);
  assert.ok(view.node.textContent.includes(checked));
  assert.equal(reads(), 2);
});

test("a first failed index read never invents a previous snapshot", async (t) => {
  const { view, fail } = fixture(t);
  fail({ status: 503, code: "display_changed" });
  await view.refresh();
  assert.doesNotMatch(
    view.node.textContent,
    /以下保留上次结果|6789|上次成功读取状态/,
  );
});

test("revoked index access removes the previous snapshot immediately", async (t) => {
  const { view, fail } = fixture(t);
  await view.refresh();
  assert.match(view.node.textContent, /6789/);
  fail({ status: 404, code: "request_denied" });
  await view.refresh();
  assert.doesNotMatch(
    view.node.textContent,
    /6789|以下保留上次结果|上次成功读取状态/,
  );
});
