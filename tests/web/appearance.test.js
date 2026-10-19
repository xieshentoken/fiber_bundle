import test from "node:test";
import assert from "node:assert/strict";
import {
  APPEARANCE_KEY,
  DEFAULT_APPEARANCE,
  normalizeAppearance,
  readAppearance,
  writeAppearance,
} from "../../src/fiber_bundle/web/appearance.js";

test("appearance accepts independent script sizes and drops unknown/private fields", () => {
  const value = normalizeAppearance({
    cjkSize: 18,
    latinSize: 13,
    cjkFont: "serif",
    theme: "green",
    password: "synthetic",
    csrf: "synthetic",
  });
  assert.equal(value.cjkSize, 18);
  assert.equal(value.latinSize, 13);
  assert.equal(value.theme, "clay");
  assert.equal(value.defaultTheme, "clay");
  assert.equal("password" in value, false);
  assert.equal("csrf" in value, false);
  assert.deepEqual(
    normalizeAppearance({
      cjkSize: "18",
      latinSize: 0,
      theme: "url(external)",
      cjkFont: "arbitrary",
    }),
    DEFAULT_APPEARANCE,
  );
});
test("corrupt or unavailable storage falls back without changing defaults", () => {
  assert.deepEqual(
    readAppearance({ getItem: () => "{broken" }),
    DEFAULT_APPEARANCE,
  );
  assert.deepEqual(
    readAppearance({
      getItem() {
        throw new Error("blocked");
      },
    }),
    DEFAULT_APPEARANCE,
  );
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key),
    setItem: (key, value) => values.set(key, value),
  };
  writeAppearance(storage, {
    cjkSize: 20,
    latinSize: 12,
    density: "compact",
    token: "synthetic",
  });
  assert.equal(readAppearance(storage).cjkSize, 20);
  assert.equal(JSON.parse(values.get(APPEARANCE_KEY)).latinSize, 12);
  assert.equal(values.get(APPEARANCE_KEY).includes("token"), false);
  assert.throws(
    () =>
      writeAppearance(
        {
          setItem() {
            throw new Error("blocked");
          },
        },
        {},
      ),
    /blocked/,
  );
});
test("selected local font names persist alongside old built-in preferences", () => {
  const font = 'local:["PingFang SC","PingFangSC-Regular"]';
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key),
    setItem: (key, value) => values.set(key, value),
  };
  writeAppearance(storage, { cjkFont: font, latinFont: "serif", cjkSize: 19 });
  assert.equal(readAppearance(storage).cjkFont, font);
  assert.equal(readAppearance(storage).latinFont, "serif");
  assert.equal(readAppearance(storage).cjkSize, 19);
  for (const invalid of [
    'local:{"url":"https://example.com"}',
    'local:["A"]',
    "url(remote)",
    'local:["A","bad\\nname"]',
  ])
    assert.equal(normalizeAppearance({ cjkFont: invalid }).cjkFont, "noto");
});
