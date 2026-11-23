import { installDOM } from "./reliability_dom_fixture.js";
export function graphDOM() {
  const dom = installDOM(),
    originalNode = globalThis.Node;
  const Element = window.Element;
  globalThis.Node = Element;
  document.createElementNS = (_ns, name) => document.createElement(name);
  Object.defineProperty(Element.prototype, "id", {
    configurable: true,
    get() {
      return this.getAttribute("id") || "";
    },
    set(value) {
      this.setAttribute("id", value);
    },
  });
  Object.defineProperty(Element.prototype, "firstElementChild", {
    get() {
      return this.firstChild;
    },
  });
  Element.prototype.after = function (...nodes) {
    const parent = this.parentNode;
    const next = parent.children[parent.children.indexOf(this) + 1];
    for (const node of nodes) {
      if (next) next.before(node);
      else parent.append(node);
    }
  };
  Element.prototype.insertBefore = function (node, before) {
    if (before) before.before(node);
    else this.append(node);
  };
  Element.prototype.insertAdjacentElement = function (_position, node) {
    this.parentNode.append(node);
  };
  Element.prototype.click = function () {
    return this.disabled ? undefined : this.listeners.click?.({ target: this });
  };
  const events = new Map();
  window.addEventListener = (key, listener) => events.set(key, listener);
  window.removeEventListener = (key, listener) => {
    if (events.get(key) === listener) events.delete(key);
  };
  window.confirm = () => true;
  window.scrollBy = () => {};
  function node(tag, id, parent) {
    const result = document.createElement(tag);
    if (id) result.id = id;
    parent.append(result);
    return result;
  }
  const panel = node("section", "graph-panel", document.body);
  const notice = node("p", "notice", document.body);
  notice.dataset.page = "graph";
  const bar = node("div", null, panel);
  bar.className = "scope-bar";
  for (const id of ["graph-client", "graph-vault"])
    node("select", id, node("label", null, bar));
  for (const id of ["graph-refresh", "graph-toggle"]) node("button", id, bar);
  node("p", "graph-policy", panel);
  const noteStep = node("section", null, panel);
  noteStep.className = "graph-step";
  for (const id of ["graph-notes", "graph-jobs"]) node("div", id, noteStep);
  node("button", "graph-submit", noteStep);
  const step = node("section", null, panel);
  step.className = "graph-step";
  const filter = node("select", "graph-filter", node("label", null, step));
  for (const value of ["moc", "all"]) {
    const option = node("option", null, filter);
    option.value = value;
  }
  for (const id of ["graph-list-mode", "graph-map-mode"])
    node("button", id, step);
  node("div", "graph-content", step);
  const canvas = node("section", "graph-canvas-panel", step);
  canvas.hidden = true;
  for (const id of [
    "graph-canvas",
    "graph-node-detail",
    "graph-statistics",
    "graph-node-list",
    "graph-search-results",
    "graph-map-search-form",
  ])
    node("div", id, canvas);
  for (const id of [
    "graph-map-refresh",
    "graph-map-search",
    "graph-layout-toggle",
    "graph-fit",
    "graph-reset-view",
    "graph-unpin",
  ])
    node("button", id, canvas);
  node("input", "graph-map-search-text", canvas);
  const moc = node("section", null, panel);
  moc.className = "graph-step";
  for (const id of ["graph-moc-form", "graph-writebacks"]) node("div", id, moc);
  return {
    ...dom,
    events,
    restore() {
      document.body.replaceChildren();
      globalThis.Node = originalNode;
      dom.restore();
    },
  };
}
export const nextTurn = async () => {
  for (let i = 0; i < 30; i++) await Promise.resolve();
};
export function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}
export const clientList = [
  { id: "no-grants", name: "No grants", active: true, grants: [] },
  {
    id: "c",
    name: "Owner",
    active: true,
    grants: [
      { vault_id: "a", can_query: true },
      { vault_id: "b", can_query: true },
    ],
  },
];
export const vaultList = [
  { id: "a", name: "Alpha", status: "active" },
  { id: "b", name: "Beta", status: "active" },
];
export function apiFixture() {
  const calls = [],
    catalog = {
      jobs: [],
      items: [],
      mocs: [],
      total: 0,
      next_offset: null,
      policy: {
        revision: 1,
        settings: {
          mode: "off",
          include: [],
          exclude: [],
          conditions: [],
          rules: [],
        },
      },
    };
  const api = {
    clients: async () => {
      calls.push(["clients"]);
      return clientList;
    },
    vaults: async () => {
      calls.push(["vaults"]);
      return vaultList;
    },
    organization: {
      list: async (scope) => {
        calls.push(["organization", scope.vault_id]);
        return catalog;
      },
      progress: async () => ({ jobs: [] }),
    },
    graph: {
      policy: async () => ({
        configured: true,
        enabled: true,
        value: { engine: { model: { name: "synthetic" } } },
      }),
      notes: async (_client, vault) => {
        calls.push(["notes", vault]);
        return [
          {
            note_id: vault,
            title: "note-" + vault,
            note_path: vault + ".md",
            selectable: true,
            sha256: "sha-" + vault,
          },
        ];
      },
      suggestions: async (_client, vault) => {
        calls.push(["suggestions", vault]);
        return { items: [], configuration: "cfg" };
      },
      jobs: async (_client, vault) => {
        calls.push(["jobs", vault]);
        return [];
      },
      intents: async (_client, vault) => {
        calls.push(["mocs", vault]);
        return [];
      },
    },
  };
  return { api, calls };
}

// Model browser event bubbling and native <dialog> close/cancel behavior while
// exercising the real dialog and select_controls implementations above this DOM.
export function browserInteractions() {
  const Element = window.Element;
  class Event {
    constructor(type, options = {}) {
      this.type = type;
      Object.assign(this, options);
      this.defaultPrevented = false;
    }
    preventDefault() {
      this.defaultPrevented = true;
    }
    stopPropagation() {
      this.stopped = true;
    }
  }
  window.Event = Event;
  window.HTMLDialogElement = Element;
  window.innerHeight = 900;
  window.innerWidth = 1200;
  Element.prototype.getClientRects = function () {
    return [this.getBoundingClientRect()];
  };
  Element.prototype.dispatchEvent = function (event) {
    event.target ||= this;
    for (
      let current = this;
      current;
      current = event.bubbles && !event.stopped ? current.parentNode : null
    )
      current.listeners[event.type]?.(event);
    if (
      event.type === "cancel" &&
      !event.defaultPrevented &&
      this.tagName === "DIALOG"
    )
      this.close();
    return !event.defaultPrevented;
  };
  Object.defineProperty(Element.prototype, "open", {
    configurable: true,
    get() {
      return this.hasAttribute("open");
    },
    set(value) {
      if (value) this.setAttribute("open", "");
      else this.removeAttribute("open");
    },
  });
  Element.prototype.showModal = function () {
    this.open = true;
  };
  Element.prototype.close = function () {
    if (!this.open) return;
    this.open = false;
    this.dispatchEvent(new Event("close"));
  };
}
