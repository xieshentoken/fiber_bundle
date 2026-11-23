import test from "node:test";
import assert from "node:assert/strict";
import { createView } from "../../src/fiber_bundle/web/atlas/view_ask.js";

// Minimal DOM fixture for the existing view's async ownership and loading states.
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
      checked: false,
      title: "",
      type: "",
    });
  }
  append(...children) {
    for (const child of children) {
      if (child instanceof Element) child.parentElement = this;
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    this.children = [];
    this.append(...children);
    if (this.tagName === "select") this.value = this.children[0]?.value || "";
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  getAttribute(name) {
    return this.attributes[name];
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }
  dispatch(name) {
    return this.listeners[name]?.({ preventDefault() {} });
  }
  querySelector(selector) {
    const cls = selector.match(/\.([\w-]+)/)?.[1];
    return (
      walk(this).find(
        (node) => cls && node.className.split(" ").includes(cls),
      ) || null
    );
  }
  insertAdjacentElement(_position, element) {
    this.parentElement.append(element);
  }
  remove() {
    if (this.parentElement)
      this.parentElement.children = this.parentElement.children.filter(
        (node) => node !== this,
      );
  }
  focus() {}
}
function walk(node) {
  return node.children.flatMap((child) =>
    child instanceof Element ? [child, ...walk(child)] : [],
  );
}
function text(node) {
  return node instanceof Element
    ? node.children.map(text).join(" ")
    : String(node);
}
function environment(t) {
  const document = {
    body: new Element("body"),
    createElement: (tag) => new Element(tag),
    createElementNS: (_ns, tag) => new Element(tag),
  };
  for (const [key, value] of Object.entries({ document, Node: Element })) {
    const before = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, {
      value,
      writable: true,
      configurable: true,
    });
    t.after(() => {
      if (before) Object.defineProperty(globalThis, key, before);
      else delete globalThis[key];
    });
  }
  t.mock.timers.enable({ apis: ["setTimeout"] });
}
const response = (name) => ({
  status: "evidence_found",
  searched_vault_ids: ["vault"],
  results: [
    {
      vault_id: "vault",
      note_path: "note.md",
      title: name,
      snippet: name,
      line: 1,
    },
  ],
});
const settle = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};
function fixture() {
  const requests = [];
  const view = createView(
    {
      api: {
        queryPolicy: async () => null,
        searchNotes(clientId, query, limit, { signal }) {
          return new Promise((resolve, reject) =>
            requests.push({ clientId, query, limit, signal, resolve, reject }),
          );
        },
      },
      store: {
        clients: async () =>
          ["one", "two"].map((id) => ({
            id,
            name: id,
            active: true,
            grants: [{ can_query: true }],
          })),
        vaults: async () => [{ id: "vault", name: "Vault" }],
        components: async () => ({ gui_features: { note_search: true } }),
      },
      navigate() {},
    },
    { params: { q: "synthetic" } },
  );
  return { view, requests };
}

test("note search failure clears loading and allows an explicit retry", async (t) => {
  environment(t);
  const { view, requests } = fixture();
  const pending = view.refresh();
  await settle();
  assert(view.node.querySelector(".a-skeleton"));
  requests[0].reject(
    Object.assign(Error("Synthetic failed request"), { code: "network" }),
  );
  await pending;
  assert.equal(view.node.querySelector(".a-skeleton"), null);
  assert(view.node.querySelector(".a-inline-error"));
  const form = view.node.querySelector(".a-ask-hero");
  form.dispatch("submit");
  await settle();
  requests[1].resolve(response("Retried result"));
  await settle();
  assert(text(view.node).includes("Retried result"));
  assert.equal(view.node.querySelector(".a-skeleton"), null);
  view.dispose();
});

test("identity changes clear results and discard even a late successful old response", async (t) => {
  environment(t);
  const { view, requests } = fixture();
  const pending = view.refresh();
  await settle();
  const form = view.node.querySelector(".a-ask-hero");
  const identity = walk(form).find((node) => node.tagName === "select");
  identity.value = "two";
  identity.dispatch("change");
  assert.equal(requests[0].signal.aborted, true);
  requests[0].resolve(response("Wrong identity result"));
  await pending;
  assert(!text(view.node).includes("Wrong identity result"));
  assert.equal(view.node.querySelector(".a-skeleton"), null);
  form.dispatch("submit");
  await settle();
  assert.equal(requests[1].clientId, "two");
  requests[1].resolve(response("Current identity result"));
  await settle();
  assert(text(view.node).includes("Current identity result"));
  view.dispose();
});

test("editing a query or disposing the view cancels pending results without stale errors", async (t) => {
  environment(t);
  for (const action of ["input", "dispose"]) {
    const { view, requests } = fixture();
    const pending = view.refresh();
    await settle();
    if (action === "dispose") view.dispose();
    else {
      const input = walk(view.node).find(
        (node) => node.tagName === "input" && node.type === "search",
      );
      input.value = "changed";
      input.dispatch("input");
    }
    requests[0].reject(Error("Old request failure"));
    await pending;
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(view.node.querySelector(".a-skeleton"), null);
    assert.equal(view.node.querySelector(".a-inline-error"), null);
    view.dispose();
  }
});

test("search reports monotonic submit-to-result time and discards it on input changes", async (t) => {
  environment(t);
  let now = 1000;
  t.mock.method(performance, "now", () => now);
  const { view, requests } = fixture();
  const pending = view.refresh();
  await settle();
  assert(!text(view.node).includes("检索用时"));
  now = 2250;
  requests[0].resolve(response("Timed result"));
  await pending;
  assert(text(view.node).includes("Timed result"));
  assert(text(view.node).includes("检索用时 1.25 秒"));
  const input = walk(view.node).find(
    (node) => node.tagName === "input" && node.type === "search",
  );
  input.value = "changed";
  input.dispatch("input");
  assert(!text(view.node).includes("检索用时"));
  view.dispose();
});
