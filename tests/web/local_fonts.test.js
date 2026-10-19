import test from "node:test";
import assert from "node:assert/strict";
import {
  fontCatalog,
  localFont,
  createLocalFontFaces,
} from "../../src/fiber_bundle/web/local_fonts.js";

test("installed faces become sorted family choices with a regular face preferred", () => {
  const values = fontCatalog([
    { family: "Zulu", postscriptName: "Zulu-Bold", style: "Bold" },
    { family: "Alpha", postscriptName: "Alpha-Italic", style: "Italic" },
    { family: "Zulu", postscriptName: "Zulu-Regular", style: "Regular" },
    { family: "Alpha", postscriptName: "Alpha-Book", style: "Book" },
    { family: ".Private", postscriptName: "Private" },
    { family: "Invalid", postscriptName: "" },
    { family: "Bad\nName", postscriptName: "Bad" },
    null,
  ]);
  assert.deepEqual(
    values.map((font) => font.family),
    ["Alpha", "Zulu"],
  );
  assert.deepEqual(localFont(values[1].value), {
    family: "Zulu",
    postscriptName: "Zulu-Regular",
  });
  assert.equal(values[0].postscriptName, "Alpha-Book");
  assert.throws(() => fontCatalog({}), /invalid_font_catalog/);
  assert.throws(() => fontCatalog(Array(16385)), /invalid_font_catalog/);
});

test("local source quoting, independent size and removal on failure keep packaged fallback usable", async () => {
  const originalDocument = globalThis.document,
    originalWindow = globalThis.window;
  const fonts = new Set();
  globalThis.document = { fonts };
  globalThis.window = {
    FontFace: class {
      constructor(family, source, descriptors) {
        Object.assign(this, { family, source, descriptors });
      }
      load() {
        return this.source.includes("Missing")
          ? Promise.reject(new Error("not installed"))
          : Promise.resolve(this);
      }
    },
  };
  try {
    const apply = createLocalFontFaces();
    const unusual = 'Family"),url(https://example.com)';
    const font = "local:" + JSON.stringify(["Family", unusual]);
    const cjk = apply("cjk", font, 20 / 14);
    assert.equal(await cjk.ready, true);
    const cjkFace = [...fonts][0];
    assert.equal(cjkFace.source, `local(${JSON.stringify(unusual)})`);
    assert.equal(cjkFace.descriptors.sizeAdjust, `${(20 / 14) * 100}%`);
    assert.match(cjkFace.descriptors.unicodeRange, /2E80-9FFF/);
    const latin = apply("latin", 'local:["Latin","Latin-Regular"]');
    assert.equal(await latin.ready, true);
    assert.equal(fonts.size, 2);
    apply("cjk", font, 1);
    assert.equal(fonts.size, 2);
    assert.equal(fonts.has(cjkFace), false);
    assert.equal(
      [...fonts].find((face) => face.family === "FiberCJK-local").descriptors
        .sizeAdjust,
      "100%",
    );
    const missing = apply("cjk", 'local:["Missing","Missing-Regular"]');
    assert.equal(await missing.ready, false);
    assert.equal(fonts.size, 1);
    assert.equal(apply("latin", "dm").family, null);
    assert.equal(fonts.size, 0);
  } finally {
    globalThis.document = originalDocument;
    globalThis.window = originalWindow;
  }
});
