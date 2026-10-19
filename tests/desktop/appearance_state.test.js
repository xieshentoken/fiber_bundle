import test from "node:test";
import assert from "node:assert/strict";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  appearancePath,
  createAppearanceState,
  themePresentation,
  trustedAppearance,
  validAppearance,
} from "../../desktop/appearance_state.mjs";

const backend = "http://127.0.0.1:18786/";
const otherBackend = "http://127.0.0.1:18787/";
const offline = "file:///synthetic/desktop/offline.html";

test("appearance bridge accepts only five exact theme ids and two fields", () => {
  for (const theme of ["oracle", "tesla", "cursor", "clay", "dark"]) {
    assert(validAppearance({ theme, defaultTheme: "dark" }));
    assert.equal(
      themePresentation(theme).themeSource,
      theme === "dark" ? "dark" : "light",
    );
  }
  for (const value of [
    null,
    [],
    {},
    { theme: "dark" },
    { theme: "paper", defaultTheme: "clay" },
    { theme: "dark", defaultTheme: "system" },
    { theme: "dark", defaultTheme: "dark", path: "/synthetic" },
  ])
    assert.equal(validAppearance(value), false);
  assert.throws(() => themePresentation("paper"));
});

test("appearance IPC permits exact offline reads, while writes and business APIs keep backend-only trust", () => {
  const mainFrame = { url: backend };
  const window = { webContents: { mainFrame } };
  const event = { sender: window.webContents, senderFrame: mainFrame };
  trustedAppearance(event, window, backend, offline);
  trustedAppearance(event, window, backend, offline, true);
  mainFrame.url = offline;
  trustedAppearance(event, window, backend, offline);
  assert.throws(() => trustedAppearance(event, window, backend, offline, true));
  for (const url of [
    otherBackend,
    offline + "#fragment",
    "file:///etc/passwd",
  ]) {
    mainFrame.url = url;
    assert.throws(() => trustedAppearance(event, window, backend, offline));
  }
  mainFrame.url = backend;
  assert.throws(() =>
    trustedAppearance({ ...event, sender: {} }, window, backend, offline),
  );
  assert.throws(() =>
    trustedAppearance(
      { ...event, senderFrame: { url: backend } },
      window,
      backend,
      offline,
    ),
  );
  assert.throws(() => trustedAppearance(event, null, backend, offline));
});

test("native startup mirrors only explicit defaults and separates backend origins and profiles", () => {
  const dir = mkdtempSync(join(tmpdir(), "fiber-appearance-"));
  const second = mkdtempSync(join(tmpdir(), "fiber-appearance-other-"));
  try {
    const state = createAppearanceState(dir, backend);
    assert.deepEqual(state.snapshot(), { theme: "clay", defaultTheme: "clay" });
    assert.deepEqual(state.apply({ theme: "dark", defaultTheme: "clay" }), {
      theme: "dark",
      defaultTheme: "clay",
      defaultSaved: true,
    });
    const path = appearancePath(dir, backend);
    assert.equal(existsSync(path), false);
    assert.deepEqual(createAppearanceState(dir, backend).snapshot(), {
      theme: "clay",
      defaultTheme: "clay",
    });
    assert(state.apply({ theme: "dark", defaultTheme: "dark" }).defaultSaved);
    assert.equal(statSync(path).mode & 0o777, 0o600);
    const original = readFileSync(path, "utf8");
    assert.deepEqual(JSON.parse(original), {
      version: 1,
      backendOrigin: new URL(backend).origin,
      defaultTheme: "dark",
    });
    state.apply({ theme: "oracle", defaultTheme: "dark" });
    assert.equal(readFileSync(path, "utf8"), original);
    assert.deepEqual(state.snapshot(), {
      theme: "oracle",
      defaultTheme: "dark",
    });
    assert.deepEqual(createAppearanceState(dir, backend).snapshot(), {
      theme: "dark",
      defaultTheme: "dark",
    });
    assert.deepEqual(createAppearanceState(dir, otherBackend).snapshot(), {
      theme: "clay",
      defaultTheme: "clay",
    });
    assert.deepEqual(createAppearanceState(second, backend).snapshot(), {
      theme: "clay",
      defaultTheme: "clay",
    });
    assert.throws(() => state.apply({ theme: "green", defaultTheme: "dark" }));
    assert.deepEqual(state.snapshot(), {
      theme: "oracle",
      defaultTheme: "dark",
    });
    assert.equal(readFileSync(path, "utf8"), original);
  } finally {
    rmSync(dir, { recursive: true, force: true });
    rmSync(second, { recursive: true, force: true });
  }
});

test("malformed, oversized, foreign-origin and linked cache content cannot become startup defaults", () => {
  const dir = mkdtempSync(join(tmpdir(), "fiber-appearance-invalid-"));
  const path = appearancePath(dir, backend),
    warnings = [];
  const read = () =>
    createAppearanceState(dir, backend, (...args) => warnings.push(args));
  try {
    for (const value of [
      "{broken",
      "x".repeat(4097),
      JSON.stringify({
        version: 1,
        backendOrigin: new URL(otherBackend).origin,
        defaultTheme: "dark",
      }),
      JSON.stringify({
        version: 1,
        backendOrigin: new URL(backend).origin,
        defaultTheme: "paper",
      }),
      JSON.stringify({
        version: 1,
        backendOrigin: new URL(backend).origin,
        defaultTheme: "dark",
        theme: "dark",
      }),
    ]) {
      writeFileSync(path, value);
      assert.equal(read().snapshot().theme, "clay");
    }
    rmSync(path);
    const target = join(dir, "linked.json");
    writeFileSync(
      target,
      JSON.stringify({
        version: 1,
        backendOrigin: new URL(backend).origin,
        defaultTheme: "dark",
      }),
    );
    symlinkSync(target, path);
    assert.equal(read().snapshot().theme, "clay");
    assert(warnings.length >= 6);
    assert(warnings.every((args) => !args.join(" ").includes(dir)));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("cache write failure applies the current theme but retains the old default and cache bytes", () => {
  const dir = mkdtempSync(join(tmpdir(), "fiber-appearance-failure-"));
  const moved = dir + "-moved",
    warnings = [];
  try {
    const state = createAppearanceState(dir, backend, (...args) =>
      warnings.push(args),
    );
    state.apply({ theme: "dark", defaultTheme: "dark" });
    const before = readFileSync(appearancePath(dir, backend), "utf8");
    renameSync(dir, moved);
    assert.deepEqual(state.apply({ theme: "oracle", defaultTheme: "oracle" }), {
      theme: "oracle",
      defaultTheme: "dark",
      defaultSaved: false,
    });
    assert.deepEqual(state.snapshot(), {
      theme: "oracle",
      defaultTheme: "dark",
    });
    assert.equal(readFileSync(appearancePath(moved, backend), "utf8"), before);
    assert.equal(warnings.length, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
    rmSync(moved, { recursive: true, force: true });
  }
});
