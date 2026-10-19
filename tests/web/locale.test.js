import test from "node:test";
import assert from "node:assert/strict";
import {
  createLocalePreferences,
  LOCALE_KEY,
} from "../../src/fiber_bundle/web/i18n/preferences.js";
import {
  normalizeAppearance,
  DEFAULT_APPEARANCE,
} from "../../src/fiber_bundle/web/appearance_preferences.js";
test("new reading default does not replace previously saved 14px preferences", () => {
  assert.equal(DEFAULT_APPEARANCE.latinSize, 15);
  assert.equal(
    normalizeAppearance({ latinSize: 14, cjkSize: 14 }).latinSize,
    14,
  );
  assert.equal(normalizeAppearance({ latinSize: 14, cjkSize: 14 }).cjkSize, 14);
});
test("locale persistence is independent, validated and fails before changing state", () => {
  const writes = [],
    values = new Map([
      [LOCALE_KEY, "en"],
      ["fiber.appearance.v1", "keep-font-draft"],
    ]);
  let fail = false;
  const store = {
    getItem: (key) => values.get(key),
    setItem(key, value) {
      if (fail) throw Error("unavailable");
      writes.push(key);
      values.set(key, value);
    },
  };
  const prefs = createLocalePreferences(store);
  assert.equal(prefs.current, "en");
  prefs.select("zh-CN");
  fail = true;
  assert.throws(() => prefs.select("en"), /unavailable/);
  assert.equal(prefs.current, "zh-CN");
  assert.equal(values.get("fiber.appearance.v1"), "keep-font-draft");
  assert.deepEqual(writes, [LOCALE_KEY]);
  assert.equal(
    createLocalePreferences({
      getItem() {
        throw Error();
      },
    }).current,
    "zh-CN",
  );
  assert.equal(
    createLocalePreferences({ getItem: () => "invalid" }).current,
    "zh-CN",
  );
});
test("UI bindings update in place without translating raw user content or stale feature data", async () => {
  globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
  const { setText, uiText, setLocale, locale, text, msg } =
    await import("../../src/fiber_bundle/web/i18n/index.js");
  const label = { textContent: "" },
    filename = { textContent: "" },
    stale = { textContent: "" };
  setText(label, uiText("收件箱"));
  setText(filename, "收件箱");
  setText(stale, uiText("收件箱"));
  stale.textContent = "用户刚编辑的内容";
  let requests = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = () => {
    requests++;
    throw Error("unexpected network");
  };
  try {
    setLocale("en");
    assert.equal(locale(), "en");
    assert.equal(label.textContent, "Inbox");
    assert.equal(filename.textContent, "收件箱");
    assert.equal(stale.textContent, "用户刚编辑的内容");
    assert.equal(requests, 0);
    assert.throws(() => text("missing.key"), /unknown_locale_key/);
    assert.throws(
      () => JSON.stringify({ model: msg("common.inbox") }),
      /ui_message_not_business_data/,
    );
    setLocale("zh-CN");
    assert.equal(label.textContent, "收件箱");
  } finally {
    globalThis.fetch = originalFetch;
    delete globalThis.localStorage;
  }
});

test("catalog languages and placeholders agree; nested UI stays separate from source data", async () => {
  const { messages } =
    await import("../../src/fiber_bundle/web/i18n/catalog.js");
  for (const [key, pair] of Object.entries(messages)) {
    assert.equal(pair.length, 2, key);
    const params = (s) =>
      [...s.matchAll(/\{([^}]+)\}/g)].map((x) => x[1]).sort();
    assert.deepEqual(params(pair[0]), params(pair[1]), key);
    assert(
      !/[\u4e00-\u9fff]/.test(pair[1]) ||
        pair[1].includes("/归类") ||
        pair[1].includes("查询本库："),
      key,
    );
  }
  const {
    phrase,
    joinText,
    uiText,
    setText,
    setLocale,
    localizedDate,
    languageAliases,
  } = await import("../../src/fiber_bundle/web/i18n/index.js");
  setLocale("zh-CN");
  const node = { textContent: "" },
    date = { textContent: "" };
  setText(node, joinText(["收件箱", uiText("已完成")], " · "));
  setText(
    date,
    localizedDate(new Date("2026-10-18T09:00:00Z"), {
      weekday: "long",
      timeZone: "UTC",
    }),
  );
  setLocale("en");
  assert.equal(node.textContent, "收件箱 · Completed");
  assert.equal(date.textContent, "Sunday");
  assert.equal(
    String(phrase("已切换为{p0}。", { p0: uiText("暖陶 · Clay") })),
    "Switched to Clay · Warm terracotta.",
  );
  assert.match(languageAliases("知识库"), /Knowledge base/);
  setLocale("zh-CN");
});
test("old or failing desktop locale bridge is deduplicated and never blocks the web choice", async () => {
  const { createDesktopLocaleSync } =
    await import("../../src/fiber_bundle/web/i18n/desktop.js");
  let calls = 0,
    warnings = 0;
  const sync = createDesktopLocaleSync(
    {
      async applyLocale() {
        calls++;
        throw Error("old_main");
      },
    },
    () => warnings++,
  );
  await sync("en");
  await sync("en");
  await sync("en");
  assert.equal(calls, 1);
  assert.equal(warnings, 1);
  await sync("zh-CN");
  assert.equal(calls, 2);
  await createDesktopLocaleSync({})("en");
});

test("named source controls retain raw text even when it equals a UI message", async () => {
  const previous = globalThis.document;
  globalThis.document = {
    documentElement: {},
    createElement: () => ({ textContent: "", addEventListener() {} }),
  };
  try {
    const { setLocale, uiText, sourceText } =
      await import("../../src/fiber_bundle/web/i18n/index.js");
    const { control } = await import("../../src/fiber_bundle/web/record_ui.js");
    setLocale("en");
    const source = control("收件箱", async () => {}, { localize: false });
    const command = control("收件箱", async () => {});
    assert.equal(source.textContent, "收件箱");
    assert.equal(String(uiText(sourceText("收件箱"))), "收件箱");
    assert.equal(String(uiText(sourceText("已完成"))), "已完成");
    assert.throws(
      () => JSON.stringify(sourceText("正文")),
      /ui_message_not_business_data/,
    );
    assert.equal(command.textContent, "Inbox");
    assert.equal(
      String(uiText("WebGL 不可用，请使用笔记列表。")),
      "WebGL unavailable. Use the note list.",
    );
    setLocale("zh-CN");
  } finally {
    if (previous === undefined) delete globalThis.document;
    else globalThis.document = previous;
  }
});
