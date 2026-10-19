import test from "node:test";
import assert from "node:assert/strict";
import { createAppearancePreferences } from "../../src/fiber_bundle/web/appearance_preferences.js";
import { createDesktopAppearanceSync } from "../../src/fiber_bundle/web/desktop_appearance.js";

test("browser without a native appearance bridge remains usable", async () => {
  const warnings = [];
  await createDesktopAppearanceSync(undefined, (v) => warnings.push(v))({
    theme: "dark",
    defaultTheme: "clay",
  });
  assert.deepEqual(warnings, []);
});
test("bridge serializes presentation changes only and ignores font-only updates", async () => {
  const calls = [];
  const sync = createDesktopAppearanceSync({
    applyAppearance: async (value) => {
      calls.push(value);
      return { defaultSaved: true };
    },
  });
  sync({
    theme: "dark",
    defaultTheme: "clay",
    latinSize: 20,
    password: "must-not-cross",
  });
  sync({ theme: "dark", defaultTheme: "clay", latinSize: 17 });
  await sync({ theme: "dark", defaultTheme: "dark" });
  assert.deepEqual(calls, [
    { theme: "dark", defaultTheme: "clay" },
    { theme: "dark", defaultTheme: "dark" },
  ]);
});
test("failed unchanged state is deduplicated; changed state and fresh load can retry", async () => {
  let fail = true;
  const warnings = [],
    calls = [];
  const sync = createDesktopAppearanceSync(
    {
      applyAppearance: async (value) => {
        calls.push(value);
        return { defaultSaved: !fail };
      },
    },
    (v) => warnings.push(v),
  );
  const value = { theme: "dark", defaultTheme: "dark" };
  await sync(value);
  assert.equal(warnings.length, 1);
  await sync(value);
  assert.equal(calls.length, 1);
  fail = false;
  await sync({ theme: "clay", defaultTheme: "dark" });
  assert.equal(calls.length, 2);
  assert.equal(warnings.length, 1);
  await createDesktopAppearanceSync({
    applyAppearance: async (v) => {
      calls.push(v);
      return { defaultSaved: true };
    },
  })(value);
  assert.equal(calls.length, 3);
});
test("rejected IPC is handled and does not break subsequent sync", async () => {
  const warnings = [];
  let count = 0;
  const sync = createDesktopAppearanceSync(
    {
      applyAppearance: async () => {
        if (count++ === 0) throw Error("offline");
        return { defaultSaved: true };
      },
    },
    (v) => warnings.push(v),
  );
  await sync({ theme: "clay", defaultTheme: "clay" });
  await sync({ theme: "dark", defaultTheme: "dark" });
  assert.equal(count, 2);
  assert.equal(warnings.length, 1);
});

test("navigation preview cleanup does not retry or re-notify a failed desktop sync", async () => {
  const values = new Map(),
    diagnostics = [];
  let calls = 0;
  const sync = createDesktopAppearanceSync(
    {
      applyAppearance: async () => {
        calls++;
        throw Error("No handler registered");
      },
    },
    (error) => diagnostics.push(error),
  );
  const prefs = createAppearancePreferences(
    {
      getItem: (k) => values.get(k) ?? null,
      setItem: (k, v) => values.set(k, v),
    },
    (value) => sync(value),
  );
  await sync(prefs.current);
  for (let n = 0; n < 5; n++) await prefs.cancel();
  assert.equal(calls, 1);
  assert.equal(diagnostics.length, 1);
  assert.match(diagnostics[0].message, /No handler registered/);
  prefs.apply({ ...prefs.current, latinSize: 19 });
  await prefs.cancel();
  assert.equal(calls, 1);
  assert.equal(prefs.saved.defaultTheme, "clay");
  prefs.destroy();
});
