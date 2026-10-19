import { desktopText, retryText } from "../../desktop/locale_copy.mjs";
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { setImmediate } from "node:timers/promises";
import { runInNewContext } from "node:vm";

const script = readFileSync(
  new URL("../../desktop/offline.js", import.meta.url),
  "utf8",
);

function offline(snapshot, language) {
  const root = { dataset: {} },
    status = {},
    scheduled = [],
    cleared = [];
  let onPagehide;
  runInNewContext(
    "(async () => {" + script.replace(/^import .*?;\n/, "") + "})()",
    {
      desktopText,
      retryText,
      document: {
        documentElement: root,
        getElementById: () => status,
        querySelectorAll: () => [],
      },
      window: {
        location: {},
        fiberDesktop: snapshot && {
          appearanceSnapshot: snapshot,
          localeSnapshot: async () => ({ language: language || "zh-CN" }),
          applyAppearance: () =>
            assert.fail("Offline cannot change saved preferences"),
          openNote: () => assert.fail("Offline cannot access notes"),
        },
        addEventListener: (event, handler) => {
          assert.equal(event, "pagehide");
          onPagehide = handler;
        },
      },
      setTimeout: (callback, delay) => {
        scheduled.push({ callback, delay });
        return scheduled.length;
      },
      clearTimeout: (timer) => cleared.push(timer),
    },
  );
  return { root, status, scheduled, cleared, pagehide: () => onPagehide() };
}

test("offline uses the read-only native current theme and keeps retry cleanup", async () => {
  const page = offline(async () => ({ theme: "dark", defaultTheme: "clay" }));
  await setImmediate();
  assert.equal(page.root.dataset.desktop, "offline");
  assert.equal(page.root.dataset.theme, "dark");
  assert.match(page.status.textContent, /15 秒/);
  assert(page.cleared.includes(1));
  page.pagehide();
  assert(page.cleared.includes(page.scheduled.length));
});

test("offline unsupported, rejected, malformed and unresponsive bridges fall back without blanking", async () => {
  for (const snapshot of [
    undefined,
    async () => {
      throw new Error("synthetic denial");
    },
    async () => ({ theme: "paper" }),
  ]) {
    const page = offline(snapshot);
    await setImmediate();
    assert.equal(page.root.dataset.theme, "clay");
  }
  const page = offline(() => new Promise(() => {}));
  assert.equal(page.root.dataset.theme, undefined);
  assert.equal(page.scheduled[0].delay, 1000);
  page.scheduled[0].callback();
  await setImmediate();
  assert.equal(page.root.dataset.theme, "clay");
});

test("offline language uses only the read-only locale mirror", async () => {
  const page = offline(async () => ({ theme: "clay" }), "en");
  await setImmediate();
  assert.equal(page.root.lang, "en");
  assert.match(page.status.textContent, /15 seconds/);
  page.pagehide();
});
