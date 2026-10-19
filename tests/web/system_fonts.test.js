import test from "node:test";
import assert from "node:assert/strict";
import {
  createSystemApi,
  validSystemFonts,
} from "../../src/fiber_bundle/web/system_api.js";

test("font catalog contract bounds OS metadata and rejects file payloads", () => {
  const font = {
    family: "中文 Sans",
    postscriptName: "Sans-Regular",
    style: "Regular",
  };
  assert.equal(validSystemFonts({ supported: true, fonts: [font] }), true);
  assert.equal(validSystemFonts({ supported: false, fonts: [] }), true);
  for (const value of [
    null,
    {},
    { supported: false, fonts: [font] },
    { supported: true, fonts: [{ ...font, path: "/private/font.ttf" }] },
    { supported: true, fonts: [{ ...font, family: "bad\nname" }] },
    { supported: true, fonts: [{ ...font, style: "" }] },
    { supported: true, fonts: Array(16385).fill(font) },
  ])
    assert.equal(validSystemFonts(value), false);
});

test("font listing uses the authenticated read-only HTTP boundary", () => {
  const calls = [];
  const api = createSystemApi((...args) => {
    calls.push(args);
    return "read";
  });
  assert.equal(api.systemFonts(), "read");
  assert.deepEqual(calls, [
    ["/admin/system/fonts", "GET", undefined, validSystemFonts],
  ]);
});
