import test from "node:test";
import assert from "node:assert/strict";
import { processingArgs } from "../../src/fiber_bundle/executor/processing_args.mjs";

test("processing uses only bundled skill paths and bounded file tools", () => {
  for (const kind of ["article", "video", "expert"]) {
    const args = processingArgs(kind);
    assert.ok(args.includes(`/opt/fiber/${kind}-processing.md`));
    assert.equal(args.at(-1), "read,write,edit");
    assert.ok(args.includes("--offline"));
    assert.ok(args.includes("--no-context-files"));
    assert.ok(!args.includes("bash"));
  }
  assert.throws(() => processingArgs("../../private"), /unknown/);
});
