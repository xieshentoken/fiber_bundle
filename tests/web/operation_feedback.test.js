import test from "node:test";
import assert from "node:assert/strict";
import { createOperationFeedback } from "../../src/fiber_bundle/web/operation_feedback.js";
function environment(work, kind = "backend") {
  const previous = globalThis.document;
  let next = 0,
    time = 1000,
    node;
  const timers = new Map(),
    panel = { hidden: false },
    button = {
      textContent: "刷新执行与费用",
      isConnected: true,
      closest: () => panel,
      insertAdjacentElement(where, status) {
        assert.equal(where, "afterend");
        node = status;
      },
    };
  globalThis.document = {
    createElement: () => ({
      children: [],
      append(...items) {
        this.children.push(...items);
      },
      setAttribute(key, value) {
        this[key] = value;
      },
      remove() {
        node = null;
      },
    }),
  };
  const feedback = createOperationFeedback(button, {
    kind,
    now: () => time,
    schedule(callback) {
      timers.set(++next, callback);
      return next;
    },
    cancel(id) {
      timers.delete(id);
    },
  });
  try {
    work({
      feedback,
      timers,
      panel,
      button,
      node: () => node,
      advance(ms) {
        time += ms;
        const [id, callback] = timers.entries().next().value;
        timers.delete(id);
        callback();
      },
    });
  } finally {
    globalThis.document = previous;
  }
}

test("fast operations finish without flashing waiting feedback", () =>
  environment(({ feedback, timers, node }) => {
    feedback.start();
    feedback.clear();
    assert.equal(timers.size, 0);
    assert.equal(node(), undefined);
  }));

test("slow operations show real elapsed time without an invented percentage", () =>
  environment(({ feedback, timers, advance, node }) => {
    feedback.start();
    feedback.start();
    assert.equal(timers.size, 1);
    advance(300);
    const [description, elapsed] = node().children;
    assert.equal(description.role, "status");
    assert.match(description.textContent, /等待后台确认/);
    assert.match(description.textContent, /离页不会取消/);
    assert(!description.textContent.includes("%"));
    assert.equal(elapsed["aria-hidden"], "true");
    advance(1000);
    assert.match(elapsed.textContent, /1 秒/);
    feedback.clear();
    assert.equal(timers.size, 0);
    assert.equal(node(), null);
  }));

test("hidden pages clear timers, and an obsolete callback cannot clear a new operation", () =>
  environment(({ feedback, timers, panel, advance, node }) => {
    feedback.start();
    const old = [...timers.values()][0];
    feedback.clear();
    feedback.start();
    old();
    assert.equal(timers.size, 1);
    advance(300);
    panel.hidden = true;
    advance(1000);
    assert.equal(node(), null);
    assert.equal(timers.size, 0);
  }));

test("directory selection describes the local system chooser, never a submitted backend operation", () =>
  environment(({ feedback, advance, node }) => {
    feedback.start();
    advance(300);
    const description = node().children[0].textContent;
    assert.match(description, /系统窗口/);
    assert.match(description, /尚未保存设置/);
    assert(!description.includes("等待后台"));
    assert(!description.includes("已提交操作"));
    feedback.clear();
  }, "directory"));
