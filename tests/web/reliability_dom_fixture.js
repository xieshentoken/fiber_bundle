// Small DOM contract fixture: moving nodes, reflected attributes and queued
// mutation delivery are modeled so regressions are observable through the view.
export function installDOM() {
  const original = { document: globalThis.document, window: globalThis.window };
  const observers = new Set();
  function mutation(target, attributeName) {
    for (const observer of observers) {
      if (
        !observer.root ||
        !(
          observer.root === target ||
          (observer.options.subtree && observer.root.contains(target))
        )
      )
        continue;
      if (attributeName && !observer.options.attributes) continue;
      if (
        attributeName &&
        observer.options.attributeFilter &&
        !observer.options.attributeFilter.includes(attributeName)
      )
        continue;
      if (!attributeName && !observer.options.childList) continue;
      observer.records.push({ target, attributeName, addedNodes: [] });
    }
  }
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase();
      this.children = [];
      this.dataset = {};
      this.attributes = new Map();
      this.listeners = {};
      this.style = {};
      this._text = "";
      this._value = "";
      this.className = "";
      this.classList = {
        add: (...values) => {
          this.className = [
            ...new Set([...this.className.split(" "), ...values]),
          ].join(" ");
        },
        remove: (value) => {
          this.className = this.className
            .split(" ")
            .filter((name) => name !== value)
            .join(" ");
        },
        contains: (value) => this.className.split(" ").includes(value),
      };
    }
    get childNodes() {
      return this.children;
    }
    get firstChild() {
      return this.children[0];
    }
    get parentElement() {
      return this.parentNode;
    }
    get isConnected() {
      return this === body || Boolean(this.parentNode?.isConnected);
    }
    set textContent(value) {
      this.replaceChildren();
      this._text = String(value ?? "");
    }
    get textContent() {
      return (
        this._text + this.children.map((node) => node.textContent).join("")
      );
    }
    set value(value) {
      this._value = value;
    }
    get value() {
      return this._value;
    }
    get hidden() {
      return this.attributes.has("hidden");
    }
    set hidden(value) {
      if (value) this.setAttribute("hidden", "");
      else this.removeAttribute("hidden");
    }
    get disabled() {
      return this.attributes.has("disabled");
    }
    set disabled(value) {
      if (value) this.setAttribute("disabled", "");
      else this.removeAttribute("disabled");
    }
    setAttribute(name, value) {
      this.attributes.set(name, String(value));
      mutation(this, name);
    }
    getAttribute(name) {
      return this.attributes.get(name) ?? null;
    }
    hasAttribute(name) {
      return this.attributes.has(name);
    }
    removeAttribute(name) {
      if (this.attributes.delete(name)) mutation(this, name);
    }
    append(...nodes) {
      for (const node of nodes) {
        if (typeof node === "string") {
          this._text += node;
          continue;
        }
        node.remove();
        node.parentNode = this;
        this.children.push(node);
      }
      mutation(this);
    }
    prepend(...nodes) {
      const old = [...this.children];
      this.replaceChildren(...nodes, ...old);
    }
    replaceChildren(...nodes) {
      for (const node of this.children) node.parentNode = null;
      this.children = [];
      this._text = "";
      this.append(...nodes);
    }
    remove() {
      if (this.parentNode) {
        const parent = this.parentNode;
        parent.children = parent.children.filter((node) => node !== this);
        this.parentNode = null;
        mutation(parent);
      }
    }
    before(node) {
      const parent = this.parentNode;
      node.remove();
      node.parentNode = parent;
      parent.children.splice(parent.children.indexOf(this), 0, node);
      mutation(parent);
    }
    replaceWith(node) {
      this.before(node);
      this.remove();
    }
    contains(node) {
      return (
        this === node || this.children.some((child) => child.contains(node))
      );
    }
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    }
    removeEventListener(name) {
      delete this.listeners[name];
    }
    matches(selector) {
      return selector.split(",").some((part) => {
        if (part === ":disabled") return this.disabled;
        if (/^#[\w-]+$/.test(part)) return this.id === part.slice(1);
        const tag = part.match(/^[\w-]+/)?.[0];
        if (tag && tag.toUpperCase() !== this.tagName) return false;
        for (const match of part.matchAll(/\.([\w-]+)/g))
          if (!this.classList.contains(match[1])) return false;
        for (const match of part.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
          const value = match[1].startsWith("data-")
            ? this.dataset[
                match[1]
                  .slice(5)
                  .replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())
              ]
            : this.getAttribute(match[1]);
          if (value == null || (match[2] !== undefined && value !== match[2]))
            return false;
        }
        return (
          !part.includes(":not") &&
          Boolean(tag || part.includes(".") || part.includes("["))
        );
      });
    }
    closest(selector) {
      for (let node = this; node; node = node.parentNode)
        if (node.matches(selector)) return node;
      return null;
    }
    querySelectorAll(selector) {
      return this.children.flatMap((child) => [
        ...(child.matches(selector) ? [child] : []),
        ...child.querySelectorAll(selector),
      ]);
    }
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    }
    scrollIntoView() {}
    getBoundingClientRect() {
      return { top: 0, height: 400, width: 800 };
    }
    focus() {
      globalThis.document.activeElement = this;
    }
    get max() {
      return Number(this.getAttribute("max") || 1);
    }
    set max(value) {
      this.setAttribute("max", value);
    }
  }
  class Select extends Element {
    constructor() {
      super("select");
      this._selectedIndex = 0;
      this.validity = { valid: true };
    }
    get options() {
      return this.children;
    }
    get selectedIndex() {
      return this.children.length ? this._selectedIndex : -1;
    }
    set selectedIndex(value) {
      this._selectedIndex = value;
    }
    get value() {
      return this.options[this.selectedIndex]?.value || "";
    }
    set value(value) {
      const index = this.options.findIndex((option) => option.value === value);
      this.selectedIndex = index;
    }
  }
  class Progress extends Element {
    constructor() {
      super("progress");
    }
    get value() {
      return Math.min(
        this.max,
        Math.max(0, Number(this.getAttribute("value") || 0)),
      );
    }
    set value(value) {
      this.setAttribute("value", value);
    }
  }
  class MutationObserver {
    constructor(callback) {
      this.callback = callback;
      this.records = [];
      observers.add(this);
    }
    observe(root, options) {
      this.root = root;
      this.options = options;
    }
    disconnect() {
      this.root = null;
      this.records = [];
    }
  }
  const body = new Element("body"),
    documentElement = new Element("html");
  const document = {
    body,
    documentElement,
    activeElement: body,
    visibilityState: "visible",
    createElement: (tag) =>
      tag === "select"
        ? new Select()
        : tag === "progress"
          ? new Progress()
          : new Element(tag),
    createTextNode: (value) => {
      const node = new Element("text");
      node.textContent = value;
      return node;
    },
    getElementById: (id) => body.querySelector("#" + id),
    querySelectorAll: (selector) => body.querySelectorAll(selector),
    querySelector: (selector) => body.querySelector(selector),
    addEventListener() {},
    removeEventListener() {},
  };
  globalThis.document = document;
  globalThis.window = {
    Element,
    HTMLSelectElement: Select,
    MutationObserver,
    addEventListener() {},
    removeEventListener() {},
    scrollX: 0,
    scrollY: 0,
    scrollTo() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
  };
  return {
    document,
    flushMutations(limit = 10) {
      let rounds = 0;
      while ([...observers].some((observer) => observer.records.length)) {
        if (++rounds > limit) throw Error("Mutation delivery did not settle");
        for (const observer of observers) {
          if (!observer.records.length) continue;
          const records = observer.records.splice(0);
          observer.callback(records);
        }
      }
      return rounds;
    },
    restore() {
      globalThis.document = original.document;
      globalThis.window = original.window;
    },
  };
}
