import test from "node:test";
import assert from "node:assert/strict";
import {
  entryTheme,
  navigateTheme,
  themeRenderer,
} from "../../src/fiber_bundle/web/theme_entry.js";
function storage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => data.set(k, v),
    removeItem: (k) => data.delete(k),
  };
}
test("one navigation selects the complete Atlas renderer without changing the startup default", () => {
  const local = storage({
      "fiber.appearance.v1": JSON.stringify({ defaultTheme: "oracle" }),
    }),
    session = storage();
  let navigations = 0;
  navigateTheme("atlas", session, () => navigations++);
  assert.equal(navigations, 1);
  assert.equal(entryTheme(local, session), "atlas");
  assert.equal(entryTheme(local, session), "oracle");
  assert.equal(themeRenderer("atlas"), "atlas");
  assert.equal(themeRenderer("oracle"), "classic");
});
test("startup default Atlas and invalid or blocked transient choices fail safely", () => {
  const local = storage({
    "fiber.appearance.v1": JSON.stringify({ defaultTheme: "atlas" }),
  });
  assert.equal(entryTheme(local, storage()), "atlas");
  assert.throws(() => navigateTheme("../evil", storage(), () => assert.fail()));
  assert.throws(
    () =>
      navigateTheme(
        "atlas",
        {
          setItem() {
            throw Error("blocked");
          },
        },
        () => assert.fail(),
      ),
    /blocked/,
  );
  assert.equal(
    entryTheme(local, storage({ "fiber.theme.navigation.v1": "../evil" })),
    "atlas",
  );
});
