import test from "node:test";
import assert from "node:assert/strict";
import {
  availableOptions,
  nextOption,
  findOption,
} from "../../src/fiber_bundle/web/select_state.js";

const options = [
  { textContent: "Personal" },
  { textContent: "不可查询", disabled: true },
  { textContent: "Supplier" },
  { textContent: "隐藏库", hidden: true },
  {
    textContent: "受限分组",
    parentElement: { tagName: "OPTGROUP", disabled: true },
  },
  { textContent: "青松资料" },
];
test("keyboard navigation skips hidden/disabled options and wraps without editing values", () => {
  assert.deepEqual(
    availableOptions(options).map(({ index }) => index),
    [0, 2, 5],
  );
  assert.equal(nextOption(options, 0, "ArrowDown"), 2);
  assert.equal(nextOption(options, 0, "ArrowUp"), 5);
  assert.equal(nextOption(options, 5, "ArrowDown"), 0);
  assert.equal(nextOption(options, 2, "Home"), 0);
  assert.equal(nextOption(options, 2, "End"), 5);
  assert.equal(nextOption([{ disabled: true }], 0, "ArrowDown"), -1);
});
test("typeahead finds Latin and Chinese labels without reaching disallowed options", () => {
  assert.equal(findOption(options, 0, "sup"), 2);
  assert.equal(findOption(options, 2, "青"), 5);
  assert.equal(findOption(options, 2, "不可"), 2);
  assert.equal(findOption(options, 5, "PERSON"), 0);
});
