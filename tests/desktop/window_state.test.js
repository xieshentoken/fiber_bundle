import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, statSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  validBounds,
  loadBounds,
  saveBounds,
} from "../../desktop/window_state.mjs";
const areas = [
  { x: 0, y: 0, width: 1440, height: 900 },
  { x: -1280, y: 0, width: 1280, height: 800 },
];
test("window restoration accepts visible secondary-display coordinates and rejects off-screen/malformed state", () => {
  const b = { x: -1000, y: 30, width: 900, height: 700 };
  assert.deepEqual(validBounds(b, areas), b);
  assert.deepEqual(validBounds({ ...b, x: 20000 }, areas), {});
  assert.deepEqual(validBounds({ ...b, width: 500 }, areas), {});
  assert.deepEqual(validBounds({ ...b, y: "30" }, areas), {});
});
test("window state round trips independently with owner-only mode and never overwrites on invalid input", () => {
  const dir = mkdtempSync(join(tmpdir(), "fiber-window-")),
    path = join(dir, "window.json"),
    b = { x: 100, y: 40, width: 1180, height: 820 };
  try {
    assert.deepEqual(loadBounds(path, areas), {});
    assert(saveBounds(path, b, areas));
    assert.deepEqual(loadBounds(path, areas), b);
    assert.equal(statSync(path).mode & 0o777, 0o600);
    const before = readFileSync(path, "utf8");
    assert.equal(saveBounds(path, { ...b, width: 1 }, areas), false);
    assert.equal(readFileSync(path, "utf8"), before);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
