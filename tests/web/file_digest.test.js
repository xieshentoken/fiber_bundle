import test from "node:test";
import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { createHash } from "node:crypto";
import { fileSha } from "../../src/fiber_bundle/web/file_digest.js";
for (const size of [
  0,
  3,
  55,
  56,
  63,
  64,
  65,
  262143,
  262144,
  262145,
  9 * 1024 * 1024,
]) {
  test(`bounded file digest agrees with SHA-256 for ${size} bytes`, async () => {
    const bytes = Buffer.alloc(size);
    for (let i = 0; i < size; i++) bytes[i] = i % 251;
    const blob = new Blob([bytes]);
    const requests = [];
    const source = {
      size: blob.size,
      slice(start, end) {
        requests.push(end - start);
        return blob.slice(start, end);
      },
    };
    assert.equal(
      await fileSha(source),
      createHash("sha256").update(bytes).digest("hex"),
    );
    assert.ok(requests.every((size) => size <= 256 * 1024));
  });
}
