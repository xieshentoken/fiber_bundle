import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
  symlinkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  createLocaleState,
  localePath,
  validLocale,
} from "../../desktop/locale_state.mjs";
import { menuTemplate, desktopText } from "../../desktop/locale_copy.mjs";
const backend = "http://127.0.0.1:18941/";
test("native locale validates exact fields and isolates origin and profile caches", () => {
  const dir = mkdtempSync(join(tmpdir(), "fiber-locale-"));
  try {
    const state = createLocaleState(dir, backend);
    assert.deepEqual(state.snapshot(), { language: "zh-CN" });
    assert.deepEqual(state.apply({ language: "en" }), {
      language: "en",
      cacheSaved: true,
    });
    assert.equal(createLocaleState(dir, backend).snapshot().language, "en");
    assert.equal(
      createLocaleState(dir, "http://127.0.0.1:18943/").snapshot().language,
      "zh-CN",
    );
    assert.equal(statSync(localePath(dir, backend)).mode & 0o777, 0o600);
    for (const value of [
      null,
      [],
      {},
      { language: "fr" },
      { language: "en", path: "/x" },
    ]) {
      assert.equal(validLocale(value), false);
      assert.throws(() => state.apply(value));
    }
    assert.equal(state.snapshot().language, "en");
    const saved = readFileSync(localePath(dir, backend), "utf8");
    for (const bad of [
      "{bad",
      "x".repeat(4097),
      saved.replace("18941", "18943"),
      saved.replace('"en"', '"fr"'),
    ]) {
      writeFileSync(localePath(dir, backend), bad);
      assert.equal(
        createLocaleState(dir, backend, () => {}).snapshot().language,
        "zh-CN",
      );
    }
    rmSync(localePath(dir, backend));
    const linked = join(dir, "linked.json");
    writeFileSync(linked, saved);
    symlinkSync(linked, localePath(dir, backend));
    assert.equal(
      createLocaleState(dir, backend, () => {}).snapshot().language,
      "zh-CN",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
test("cache failure retains current language without changing another preference", () => {
  const dir = mkdtempSync(join(tmpdir(), "fiber-locale-failure-")),
    warnings = [];
  const state = createLocaleState(dir, backend, (...x) => warnings.push(x));
  rmSync(dir, { recursive: true, force: true });
  assert.deepEqual(state.apply({ language: "en" }), {
    language: "en",
    cacheSaved: false,
  });
  assert.equal(state.snapshot().language, "en");
  assert.equal(warnings.length, 1);
});
test("native menus translate all app-owned labels while actions and roles stay equal", () => {
  const actions = { open() {}, connect() {}, quit() {}, close() {} };
  const zh = menuTemplate("zh-CN", actions),
    en = menuTemplate("en", actions);
  const walk = (x) =>
    x.flatMap((item) => [item, ...(item.submenu ? walk(item.submenu) : [])]);
  assert.deepEqual(
    walk(zh).map((x) => [x.role, x.click, x.accelerator]),
    walk(en).map((x) => [x.role, x.click, x.accelerator]),
  );
  assert(
    walk(en)
      .filter((x) => x.label)
      .every((x) => !/[\u4e00-\u9fff]/.test(x.label)),
  );
  assert.equal(desktopText("en", "unsavedTitle"), "Unsaved Changes");
});
