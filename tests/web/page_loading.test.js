import test from "node:test";
import assert from "node:assert/strict";
import { createPageLoading } from "../../src/fiber_bundle/web/page_loading.js";

function fixture() {
  let next = 0;
  const timers = new Map(),
    indicator = { hidden: true };
  const loading = createPageLoading(indicator, {
    viewportHeight: () => 820,
    schedule(callback) {
      timers.set(++next, callback);
      return next;
    },
    cancel(id) {
      timers.delete(id);
    },
  });
  const panel = {
    style: { minHeight: "" },
    attributes: {},
    getBoundingClientRect: () => ({ top: 230, height: 214 }),
    setAttribute(key, value) {
      this.attributes[key] = value;
    },
    removeAttribute(key) {
      delete this.attributes[key];
    },
  };
  return { loading, panel, indicator, timers };
}

test("quick page loads reserve visible space without flashing a loading message", () => {
  const { loading, panel, indicator, timers } = fixture();
  const finish = loading.begin(panel);
  assert.equal(panel.style.minHeight, "566px");
  assert.equal(panel.attributes["aria-busy"], "true");
  finish();
  assert.equal(timers.size, 0);
  assert.equal(indicator.hidden, true);
  assert.equal(panel.style.minHeight, "");
  assert.equal(panel.attributes["aria-busy"], undefined);
});

test("a slow page shows feedback and an older completion cannot clear a newer load", () => {
  const { loading, panel, indicator, timers } = fixture();
  const first = loading.begin(panel);
  const late = [...timers.values()][0];
  const second = loading.begin(panel);
  late();
  assert.equal(indicator.hidden, true);
  [...timers.values()][0]();
  assert.equal(indicator.hidden, false);
  first();
  assert.equal(indicator.hidden, false);
  assert.equal(panel.attributes["aria-busy"], "true");
  second();
  assert.equal(indicator.hidden, true);
});

test("leaving or signing out clears the loading delay and the reserved geometry", () => {
  const { loading, panel, indicator, timers } = fixture();
  loading.begin(panel);
  loading.clear();
  assert.equal(timers.size, 0);
  assert.equal(indicator.hidden, true);
  assert.equal(panel.style.minHeight, "");
  loading.clear();
});

test("slow empty lists show owned placeholders and clean them on failure or navigation", () => {
  const { loading, panel, timers } = fixture(),
    previous = globalThis.document,
    children = [];
  panel.querySelector = () => ({ append: (node) => children.push(node) });
  globalThis.document = {
    createElement: () => ({
      setAttribute(key, value) {
        this[key] = value;
      },
      remove() {
        this.removed = true;
      },
    }),
  };
  try {
    loading.begin(panel);
    [...timers.values()][0]();
    assert.equal(children.length, 2);
    assert(children.every((node) => node["aria-hidden"] === "true"));
    loading.clear();
    assert(children.every((node) => node.removed));
    assert.equal(timers.size, 0);
  } finally {
    globalThis.document = previous;
  }
});
