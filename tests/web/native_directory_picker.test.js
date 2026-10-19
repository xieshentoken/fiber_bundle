import test from "node:test";
import assert from "node:assert/strict";
import {
  chooseDirectory,
  fillDirectory,
} from "../../src/fiber_bundle/web/native_directory_picker.js";
function field() {
  return {
    value: "/old",
    isConnected: true,
    closest: () => null,
    events: [],
    ownerDocument: {
      defaultView: {
        Event: class {
          constructor(type, options) {
            this.type = type;
            this.bubbles = options.bubbles;
          }
        },
      },
    },
    dispatchEvent(event) {
      this.events.push(event.type);
    },
  };
}
test("native selection fills the local draft and emits input/change without saving", async () => {
  const input = field(),
    calls = [];
  assert.equal(
    await fillDirectory(input, {
      select: async (current) => {
        calls.push(current);
        return "/new/中文 folder";
      },
    }),
    true,
  );
  assert.equal(input.value, "/new/中文 folder");
  assert.deepEqual(calls, ["/old"]);
  assert.deepEqual(input.events, ["input", "change"]);
});
test("cancel leaves a typed path and draft untouched", async () => {
  const input = field();
  assert.equal(await fillDirectory(input, { select: async () => null }), false);
  assert.equal(input.value, "/old");
  assert.deepEqual(input.events, []);
});
test("timeout clears its timer and ignores a later result; another attempt is usable", async () => {
  let expire,
    finish,
    cleared = 0;
  const input = field();
  const pending = fillDirectory(input, {
    select: () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
    schedule: (callback) => {
      expire = callback;
      return 1;
    },
    cancel: () => {
      cleared++;
    },
  });
  await Promise.resolve();
  expire();
  await assert.rejects(pending, /超时/);
  finish("/late");
  await Promise.resolve();
  assert.equal(input.value, "/old");
  assert.equal(cleared, 1);
  assert(await fillDirectory(input, { select: async () => "/retry" }));
  assert.equal(input.value, "/retry");
});
test("manual edits, hidden pages and replaced form nodes are not overwritten", async () => {
  for (const alter of [
    (input) => {
      input.value = "/manual";
    },
    (input) => {
      input.isConnected = false;
    },
    (input) => {
      input.closest = () => ({});
    },
  ]) {
    const input = field();
    let finish;
    const pending = fillDirectory(input, {
      select: () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    });
    await Promise.resolve();
    alter(input);
    finish("/late");
    assert.equal(await pending, false);
    assert.notEqual(input.value, "/late");
    assert.deepEqual(input.events, []);
  }
});
test("invalid results and busy panels give actionable local messages", async () => {
  for (const result of [undefined, "relative", {}, "/bad\0path"])
    await assert.rejects(
      chooseDirectory("", { select: async () => result }),
      /原有输入已保留/,
    );
  await assert.rejects(
    chooseDirectory("", {
      select: async () => {
        throw new Error("directory_picker_busy");
      },
    }),
    /已有文件夹选择窗口/,
  );
});
