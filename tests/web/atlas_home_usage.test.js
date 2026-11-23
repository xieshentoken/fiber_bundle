import test from "node:test";
import assert from "node:assert/strict";
import {
  homeUsageCard,
  createUsageState,
  knownTokens,
  tokensText,
  costReasons,
} from "../../src/fiber_bundle/web/atlas/home_usage.js";
import { costSummary } from "../../src/fiber_bundle/web/home_model.js";
class Element {
  constructor(tagName) {
    Object.assign(this, {
      tagName,
      children: [],
      dataset: {},
      attributes: {},
      listeners: {},
      className: "",
      style: {},
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
    ? [node.textContent || "", ...node.children.map(text)].join(" ")
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

const day = {
  day: "2026-11-07",
  calls: 3,
  input_tokens: 120,
  output_tokens: 30,
  token_unknown_calls: 1,
  estimated_nusd: 0,
  reserved_nusd: 0,
  settled_calls: 0,
  unsettled_calls: 3,
  price_unknown_calls: 2,
  pending_calls: 0,
  result_unknown_calls: 1,
};
function summary() {
  return costSummary(
    { daily: [day], lifetime: { ...day }, local_calls: 0 },
    new Date("2026-11-07T12:00:00Z"),
  );
}
function button(node, label) {
  return walk(node).find(
    (n) => n.tagName === "button" && text(n).trim() === label,
  );
}
const storage = () => {
  const values = new Map();
  return { getItem: (k) => values.get(k), setItem: (k, v) => values.set(k, v) };
};
test("tokens keep reported partial usage, unknowns differ from zero, and reasons are specific", () => {
  assert.equal(knownTokens(day), 150);
  assert.match(tokensText(day), /150.*部分/);
  assert.equal(knownTokens({ ...day, token_unknown_calls: 3 }), null);
  assert.equal(knownTokens({ ...day, available: false }), null);
  assert.equal(
    knownTokens({
      ...day,
      calls: 0,
      token_unknown_calls: 0,
      input_tokens: 0,
      output_tokens: 0,
    }),
    0,
  );
  assert.equal(knownTokens({ ...day, input_tokens: -1 }), null);
  assert.equal(
    knownTokens({ ...day, input_tokens: Number.MAX_SAFE_INTEGER }),
    null,
  );
  assert.match(costReasons(day), /2.*缺少价格.*1.*结果未确认/);
  assert.match(costReasons({ ...day, pending_calls: 1 }), /尚未完成/);
  assert.match(costReasons({ unsettled_calls: 1 }), /未完整确认/);
  assert.equal(costReasons({ calls: 0 }), "");
});
test("home exposes tokens, cost and reasons; manual/poll rebuild and reload retain metric/table", (t) => {
  environment(t);
  const store = storage(),
    state = createUsageState(store),
    cost = summary();
  const frozen = JSON.stringify(cost);
  let node = homeUsageCard(cost, {}, state);
  assert.match(text(node), /累计已记录 Tokens/);
  assert.match(text(node), /150.*部分/);
  assert.ok(
    walk(node).some(
      (n) => n.tagName === "path" && n.getAttribute("class") === "a-chart-bar",
    ),
  );
  button(node, "表格").dispatch("click");
  assert.equal(state.mode, "table");
  assert.ok(walk(node).some((n) => n.tagName === "table"));
  assert.match(text(node), /缺少价格/);
  button(node, "估算费用").dispatch("click");
  assert.equal(state.metric, "cost");
  assert.ok(walk(node).some((n) => n.tagName === "table"));
  for (let i = 0; i < 3; i++) {
    node = homeUsageCard(cost, {}, state);
    assert.ok(walk(node).some((n) => n.tagName === "table"));
    assert.equal(button(node, "表格").getAttribute("aria-checked"), "true");
  }
  const restored = createUsageState(store);
  assert.equal(restored.mode, "table");
  assert.equal(restored.metric, "cost");
  node = homeUsageCard(cost, {}, restored);
  assert.ok(walk(node).some((n) => n.tagName === "table"));
  assert.equal(JSON.stringify(cost), frozen);
});
test("display storage corruption or denial cannot prevent rendering", (t) => {
  environment(t);
  for (const store of [
    { getItem: () => "broken" },
    {
      getItem: () => {
        throw Error("denied");
      },
      setItem: () => {
        throw Error("denied");
      },
    },
    { getItem: () => '{"metric":"invalid","mode":"invalid"}' },
  ]) {
    const state = createUsageState(store);
    assert.equal(state.metric, "tokens");
    assert.equal(state.mode, "chart");
    state.save();
    assert.ok(homeUsageCard(summary(), {}, state));
  }
  assert.match(
    text(homeUsageCard(null, { error: {} }, createUsageState(null))),
    /读取失败/,
  );
});
