import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import {
  createAppearancePreferences,
  DEFAULT_APPEARANCE,
  APPEARANCE_KEY,
  normalizeAppearance,
  writeAppearance,
} from "../../src/fiber_bundle/web/appearance_preferences.js";
import {
  THEMES,
  THEME_IDS,
  themeFont,
} from "../../src/fiber_bundle/web/themes.js";

function fixture(initial = {}) {
  const values = new Map(),
    renders = [],
    listeners = new Map();
  let fail = false,
    writes = 0,
    pending = null;
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem(key, value) {
      if (fail) throw new Error("blocked");
      writes++;
      values.set(key, value);
    },
  };
  writeAppearance(storage, initial);
  const control = createAppearancePreferences(
    storage,
    (value) => {
      renders.push(value);
      return pending;
    },
    {
      addEventListener: (name, fn) => listeners.set(name, fn),
      removeEventListener: (name) => listeners.delete(name),
    },
  );
  return {
    control,
    storage,
    renders,
    listeners,
    get writes() {
      return writes;
    },
    block() {
      fail = true;
    },
    delay(value) {
      pending = value;
    },
    sync() {
      listeners.get("storage")?.({ key: APPEARANCE_KEY, storageArea: storage });
    },
  };
}

test("all themes and explicit theme fonts normalize; invalid theme preserves valid other prefs", () => {
  assert.deepEqual(THEME_IDS, [
    "oracle",
    "tesla",
    "cursor",
    "clay",
    "dark",
    "atlas",
  ]);
  assert.equal(THEMES.length, 6);
  for (const theme of THEME_IDS)
    assert.equal(
      normalizeAppearance({ theme, latinFont: "theme", cjkFont: "theme" })
        .theme,
      theme,
    );
  assert.equal(
    normalizeAppearance({ theme: "unknown", cjkSize: 18 }).cjkSize,
    18,
  );
  assert.equal(normalizeAppearance({ theme: "unknown" }).theme, "clay");
  assert.equal(themeFont("oracle", "latin", true), "serif");
  assert.equal(themeFont("oracle", "latin"), "dm");
  assert.equal(themeFont("tesla", "cjk"), "system");
});
test("session switches leave drafts dirty and cancel retains the current theme", () => {
  const f = fixture({ cjkFont: 'local:["Songti SC","STSongti-SC-Regular"]' });
  f.control.apply({ ...f.control.saved, latinSize: 18, density: "compact" });
  f.control.selectTheme("oracle");
  assert.equal(f.control.saved.latinSize, 15);
  assert.equal(f.control.current.latinSize, 18);
  assert.equal(f.control.current.density, "compact");
  assert.equal(
    f.control.saved.cjkFont,
    'local:["Songti SC","STSongti-SC-Regular"]',
  );
  f.control.cancel();
  assert.equal(f.control.current.latinSize, 15);
  assert.equal(f.control.current.theme, "oracle");
});
test("stale whole-form theme never replaces a newer commit when settings save", () => {
  const f = fixture();
  const form = { ...f.control.saved, latinSize: 19 };
  f.control.apply(form);
  f.control.selectTheme("tesla");
  f.control.save(form);
  assert.equal(f.control.saved.defaultTheme, "clay");
  assert.equal(f.control.current.theme, "tesla");
  assert.equal(f.control.current.latinSize, 19);
  assert.deepEqual(f.control.current, { ...f.control.saved, theme: "tesla" });
});
test("failed theme/save commits preserve saved and preview values and don't notify success", () => {
  const f = fixture();
  let updates = 0;
  f.control.apply({ ...f.control.saved, cjkSize: 20 });
  f.control.subscribe(() => updates++);
  const before = f.control.current;
  f.block();
  assert.throws(() => f.control.setDefaultTheme("cursor"), /blocked/);
  assert.throws(() => f.control.save(before), /blocked/);
  assert.deepEqual(f.control.current, before);
  assert.equal(f.control.saved.theme, "clay");
  assert.equal(updates, 0);
  assert.throws(() => f.control.selectTheme("invalid"), /invalid_theme/);
});
test("repeated current selection doesn't write and subscribers can detach", () => {
  const f = fixture({ theme: "cursor" });
  let updates = 0;
  const unsubscribe = f.control.subscribe(() => updates++);
  const writes = f.writes;
  f.control.selectTheme("cursor");
  assert.equal(f.writes, writes);
  assert.equal(updates, 1);
  unsubscribe();
  f.control.selectTheme("oracle");
  assert.equal(updates, 1);
  f.control.destroy();
  assert.equal(f.listeners.size, 0);
});
test("storage synchronization uses latest persisted state and preserves local drafts", () => {
  const f = fixture();
  f.control.apply({ ...f.control.saved, cjkSize: 18 });
  writeAppearance(f.storage, { theme: "tesla", latinSize: 17, cjkSize: 15 });
  f.sync();
  assert.equal(f.control.current.theme, "clay");
  assert.equal(f.control.saved.defaultTheme, "tesla");
  assert.equal(f.control.current.cjkSize, 18);
  assert.equal(f.control.current.latinSize, 17);
  f.control.cancel();
  assert.equal(f.control.current.cjkSize, 15);
  f.listeners.get("storage")({ key: "unrelated", storageArea: f.storage });
  assert.equal(f.control.saved.theme, "tesla");
});
test("theme commit rereads missed cross-window updates without committing local preview", () => {
  const f = fixture();
  f.control.apply({ ...f.control.saved, latinSize: 20 });
  writeAppearance(f.storage, { cjkFont: "serif", density: "compact" });
  f.control.selectTheme("oracle");
  assert.equal(f.control.saved.cjkFont, "serif");
  assert.equal(f.control.saved.density, "compact");
  assert.equal(f.control.saved.latinSize, 15);
  assert.equal(f.control.current.latinSize, 20);
});
test("delayed settings render can't overwrite newer theme or newer draft on completion", async () => {
  const f = fixture();
  let finish;
  f.delay(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const saving = f.control.save({ latinSize: 16 });
  f.control.selectTheme("cursor");
  f.control.apply({ ...f.control.current, latinSize: 19 });
  finish();
  await saving;
  assert.equal(f.control.current.theme, "cursor");
  assert.equal(f.control.saved.defaultTheme, "clay");
  assert.equal(f.control.saved.latinSize, 16);
  assert.equal(f.control.current.latinSize, 19);
});
test("paint primer recognizes the catalogue and never writes storage, including corrupt data", () => {
  const source = readFileSync(
    new URL(
      "../../src/fiber_bundle/web/appearance_bootstrap.js",
      import.meta.url,
    ),
    "utf8",
  );
  for (const theme of [...THEME_IDS, "unknown"]) {
    const root = { dataset: {}, style: { setProperty() {} } };
    runInNewContext(source, {
      window: { localStorage: { getItem: () => JSON.stringify({ theme }) } },
      document: { documentElement: root },
    });
    assert.equal(root.dataset.theme, theme === "unknown" ? "clay" : theme);
  }
  runInNewContext(source, {
    window: { localStorage: { getItem: () => "{" } },
    document: { documentElement: { dataset: {} } },
  });
  assert.equal(DEFAULT_APPEARANCE.theme, "clay");
});

test("old themes migrate while fonts and other preferences survive", () => {
  for (const theme of ["paper", "green", "gray", "bad"]) {
    const value = normalizeAppearance({
      theme,
      latinSize: 19,
      density: "compact",
    });
    assert.equal(value.theme, "clay");
    assert.equal(value.defaultTheme, "clay");
    assert.equal(value.latinSize, 19);
    assert.equal(value.density, "compact");
    assert.equal(
      normalizeAppearance({ theme, defaultTheme: "dark" }).theme,
      "dark",
    );
  }
  for (const theme of THEME_IDS) {
    assert.equal(normalizeAppearance({ theme }).defaultTheme, theme);
  }
});
test("explicit default persists startup without committing any font draft", () => {
  const f = fixture({ theme: "oracle", latinSize: 15 });
  f.control.apply({ ...f.control.current, latinSize: 20 });
  const writes = f.writes;
  f.control.selectTheme("dark");
  assert.equal(f.writes, writes);
  assert.equal(f.control.saved.defaultTheme, "oracle");
  assert.equal(f.control.current.theme, "dark");
  f.control.setDefaultTheme();
  assert.equal(f.control.saved.defaultTheme, "dark");
  assert.equal(f.control.saved.latinSize, 15);
  assert.equal(f.control.current.latinSize, 20);
  f.control.selectTheme("tesla");
  const reopened = createAppearancePreferences(f.storage, () => {}, null);
  assert.equal(reopened.current.theme, "dark");
  assert.equal(reopened.current.latinSize, 15);
  f.control.cancel();
  assert.equal(f.control.current.theme, "tesla");
  assert.equal(f.control.current.latinSize, 15);
  reopened.destroy();
});
test("default changes from another window preserve current theme and stale font save cannot replace default", () => {
  const f = fixture({ theme: "oracle" });
  const stale = { ...f.control.current, latinSize: 20 };
  f.control.apply(stale);
  f.control.selectTheme("cursor");
  writeAppearance(f.storage, {
    theme: "dark",
    defaultTheme: "dark",
    cjkFont: "serif",
  });
  f.sync();
  assert.equal(f.control.current.theme, "cursor");
  assert.equal(f.control.saved.defaultTheme, "dark");
  assert.equal(f.control.current.latinSize, 20);
  f.control.save(stale);
  assert.equal(f.control.saved.defaultTheme, "dark");
  assert.equal(f.control.current.theme, "cursor");
  assert.equal(f.control.saved.latinSize, 20);
});
test("unavailable persistence still permits temporary switches; failed default retains prior state", () => {
  const f = fixture({ theme: "oracle" });
  f.control.apply({ ...f.control.current, cjkSize: 19 });
  f.block();
  f.control.selectTheme("dark");
  const before = f.control.current;
  assert.throws(() => f.control.setDefaultTheme(), /blocked/);
  assert.deepEqual(f.control.current, before);
  assert.equal(f.control.saved.defaultTheme, "oracle");
  assert.throws(() => f.control.setDefaultTheme("green"), /invalid_theme/);
});
test("paint primer uses default, not a temporary theme, and agrees with startup normalization", () => {
  const source = readFileSync(
    new URL(
      "../../src/fiber_bundle/web/appearance_bootstrap.js",
      import.meta.url,
    ),
    "utf8",
  );
  for (const value of [
    null,
    [],
    {},
    { theme: "green" },
    { theme: "cursor" },
    { theme: "tesla", defaultTheme: "dark" },
    { theme: "paper", defaultTheme: "oracle" },
    { theme: "dark", defaultTheme: "bad" },
  ]) {
    const root = { dataset: {}, style: { setProperty() {} } };
    runInNewContext(source, {
      window: { localStorage: { getItem: () => JSON.stringify(value) } },
      document: { documentElement: root },
    });
    assert.equal(root.dataset.theme, normalizeAppearance(value).defaultTheme);
  }
});

test("temporary read failure preserves last known defaults, fonts and drafts and aborts writes", () => {
  const f = fixture({
    theme: "oracle",
    latinFont: "serif",
    cjkFont: "system",
    latinSize: 19,
    density: "compact",
  });
  f.control.apply({ ...f.control.current, cjkSize: 18 });
  f.storage.getItem = () => {
    throw Error("read_blocked");
  };
  const before = f.control.current,
    writes = f.writes;
  f.control.selectTheme("dark");
  assert.deepEqual(f.control.current, { ...before, theme: "dark" });
  f.sync();
  assert.deepEqual(f.control.current, { ...before, theme: "dark" });
  assert.throws(() => f.control.setDefaultTheme(), /read_blocked/);
  assert.throws(
    () => f.control.save({ ...f.control.current, latinSize: 16 }),
    /read_blocked/,
  );
  assert.equal(f.writes, writes);
  assert.equal(f.control.saved.defaultTheme, "oracle");
});
test("corrupt storage cannot silently replace an existing preference on save", () => {
  const f = fixture({ theme: "oracle", latinSize: 18 });
  f.storage.setItem(APPEARANCE_KEY, "{");
  const writes = f.writes;
  f.control.selectTheme("dark");
  assert.equal(f.control.current.latinSize, 18);
  assert.throws(() => f.control.setDefaultTheme(), SyntaxError);
  assert.throws(() => f.control.save({ latinSize: 20 }), SyntaxError);
  assert.equal(f.writes, writes);
});
