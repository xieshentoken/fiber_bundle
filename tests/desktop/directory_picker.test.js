import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createDirectoryPicker } from "../../desktop/directory_picker.mjs";
function setup(home, dialog, platform = "darwin") {
  const events = [];
  const window = {
    isDestroyed: () => false,
    isMinimized: () => true,
    restore: () => events.push("restore"),
    show: () => events.push("show"),
    focus: () => events.push("focus"),
  };
  const app = { getPath: () => home, focus: () => events.push("activate") };
  const choose = createDirectoryPicker({
    app,
    dialog,
    platform,
    text: (k) => k,
  });
  return { choose, window, events };
}
test("macOS chooser activates its window and returns one existing canonical directory", async () => {
  const home = await realpath(
    await mkdtemp(join(tmpdir(), "fiber-native-picker-")),
  );
  try {
    const fixture = setup(home, {
      showOpenDialog: async (...args) => {
        assert.equal(args.length, 2);
        assert.deepEqual(args[1].properties, ["openDirectory"]);
        assert.equal(args[1].defaultPath, home);
        assert.equal(args[1].buttonLabel, "chooseDirectory");
        return { canceled: false, filePaths: [home] };
      },
    });
    assert.equal(await fixture.choose(fixture.window, home), home);
    assert.deepEqual(fixture.events.slice(0, 4), [
      "restore",
      "show",
      "activate",
      "focus",
    ]);
  } finally {
    await rm(home, { recursive: true });
  }
});
test("one pending native chooser survives a duplicate request; cancel permits another attempt", async () => {
  let finish,
    calls = 0;
  const { choose, window } = setup(tmpdir(), {
    showOpenDialog: () => {
      calls++;
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
  });
  const first = choose(window);
  assert.throws(() => choose(window), /directory_picker_busy/);
  finish({ canceled: true, filePaths: [] });
  assert.equal(await first, null);
  const second = choose(window);
  finish({ canceled: true, filePaths: [] });
  assert.equal(await second, null);
  assert.equal(calls, 2);
});
test("invalid renderer paths and malformed native results fail without persisting settings", async () => {
  const { choose, window } = setup(tmpdir(), {
    showOpenDialog: async () => ({ canceled: false, filePaths: ["relative"] }),
  });
  assert.throws(() => choose(window, "../private"), /invalid_path/);
  assert.throws(() => choose(window, { directory: "/" }), /invalid_path/);
  await assert.rejects(choose(window), /invalid_result/);
  const file = join(
    await realpath(await mkdtemp(join(tmpdir(), "fiber-file-picker-"))),
    "file",
  );
  try {
    await writeFile(file, "synthetic");
    const another = setup(tmpdir(), {
      showOpenDialog: async () => ({ canceled: false, filePaths: [file] }),
    });
    await assert.rejects(another.choose(another.window), /invalid_result/);
  } finally {
    await rm(join(file, ".."), { recursive: true });
  }
});
test("a failed panel request releases its guard, and non-macOS retains its parent", async () => {
  let calls = 0;
  const { choose, window } = setup(
    tmpdir(),
    {
      showOpenDialog: async (parent, options) => {
        assert.equal(parent, window);
        assert(options.properties.includes("openDirectory"));
        if (++calls === 1) throw new Error("native failure");
        return { canceled: true, filePaths: [] };
      },
    },
    "linux",
  );
  await assert.rejects(choose(window), /native failure/);
  assert.equal(await choose(window), null);
});
