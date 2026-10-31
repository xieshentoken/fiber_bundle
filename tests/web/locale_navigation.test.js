import test from "node:test";
import assert from "node:assert/strict";

globalThis.localStorage = { getItem: () => "zh-CN", setItem() {} };
const { setLocale, locale, text } =
  await import("../../src/fiber_bundle/web/i18n/index.js");
const { createLocaleBindings } =
  await import("../../src/fiber_bundle/web/atlas/i18n.js");
const { switchLocale } =
  await import("../../src/fiber_bundle/web/atlas/locale_switch.js");
const { pageIds, pageTitle, pageSummary, navText } =
  await import("../../src/fiber_bundle/web/atlas/nav.js");

test("Atlas navigation and page headings change synchronously before reload, then release their bindings", () => {
  setLocale("zh-CN");
  const labels = createLocaleBindings();
  const nodes = pageIds.map((page) => {
    const label = labels.text({ textContent: "" }, () => pageTitle(page));
    const heading = labels.text({ textContent: "" }, () => pageTitle(page));
    const summary = labels.text({ textContent: "" }, () => pageSummary(page));
    return { label, heading, summary };
  });
  const group = labels.text({ textContent: "" }, () =>
    navText("group_knowledge"),
  );
  const source = { textContent: "用户的中文笔记" };
  switchLocale(
    "en",
    () => {},
    () => {
      assert.equal(locale(), "en");
      for (const row of nodes) {
        assert.equal(row.label.textContent, row.heading.textContent);
        assert(!/[\u4e00-\u9fff]/.test(row.label.textContent));
        assert(!/[\u4e00-\u9fff]/.test(row.summary.textContent));
      }
      assert.equal(group.textContent, "Knowledge");
    },
  );
  assert.equal(source.textContent, "用户的中文笔记");
  setLocale("zh-CN");
  assert.equal(nodes[0].label.textContent, "首页");
  labels.dispose();
  setLocale("en");
  assert.equal(nodes[0].label.textContent, "首页");
  setLocale("zh-CN");
});

test("all classic navigation keys resolve in both directions without using stale rendered labels", async () => {
  const { readFile } = await import("node:fs/promises");
  const html = await readFile(
    new URL("../../src/fiber_bundle/web/index.html", import.meta.url),
    "utf8",
  );
  const buttons = [
    ...html.matchAll(
      /<button\b[^>]*data-tab="[^"]+"[^>]*data-i18n="([^"]+)"[^>]*>/g,
    ),
  ];
  assert(buttons.length >= 15);
  for (const language of ["en", "zh-CN", "en"]) {
    setLocale(language);
    for (const [, key] of buttons) {
      const title = text(key);
      assert.equal(/[\u4e00-\u9fff]/.test(title), language === "zh-CN", key);
    }
  }
  setLocale("zh-CN");
});
