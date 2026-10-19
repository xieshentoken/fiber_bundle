// Opt-in G3 regression: actual Electron, isolated fixture/profile, no inference.
const {
  assert,
  fs,
  path,
  base,
  output,
  login,
  navigate,
  choose,
  write,
  observe,
} = require("./gui_support.cjs");
const { _electron } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const { createHash } = require("node:crypto");
const result = {
  stage: "G3 native zoom focus regression",
  state: "RUNNING",
  real_model: false,
  checks: [],
  screenshots: [],
  page_errors: [],
  console: [],
  requests: [],
};
let desktop;
async function capture(page, name) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)),
    );
  });
  // Playwright's CSS-sized clip crops Electron surfaces when native zoom is active.
  const encoded = await desktop.evaluate(async ({ BrowserWindow }) =>
    (await BrowserWindow.getAllWindows()[0].webContents.capturePage())
      .toPNG()
      .toString("base64"),
  );
  const bytes = Buffer.from(encoded, "base64"),
    file = name + ".png";
  fs.writeFileSync(path.join(output, file), bytes);
  result.screenshots.push({
    file,
    native_capture: true,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    pixels: { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) },
  });
}
(async () => {
  try {
    const root = path.resolve(__dirname, "../.."),
      connection = process.env.FIBER_GUI_NATIVE_CONNECTION;
    assert(connection, "Select an existing synthetic connection explicitly");
    desktop = await _electron.launch({
      executablePath: path.join(
        root,
        "node_modules/electron/dist/Electron.app/Contents/MacOS/Electron",
      ),
      args: [
        path.join(root, "desktop"),
        "--url",
        base,
        "--user-data",
        path.join(output, "desktop-profile"),
      ],
      timeout: 25000,
    });
    const page = await desktop.firstWindow();
    observe(page, result);
    await login(page);
    await navigate(page, "clients");
    await choose(page, connection);
    for (const state of ["rail", "expanded"]) {
      const beforeHeight = await page
        .locator("header")
        .evaluate((n) => n.getBoundingClientRect().height);
      if ((await page.evaluate(() => document.body.dataset.sidebar)) !== state)
        await page.locator("#sidebar-toggle").click();
      const motion = await page.evaluate(async () => {
        const frames = [],
          end = performance.now() + 350;
        while (performance.now() < end) {
          await new Promise(requestAnimationFrame);
          frames.push(
            Math.abs(
              document.querySelector(".brand-area").getBoundingClientRect()
                .right -
                document.querySelector("#sidebar").getBoundingClientRect()
                  .right,
            ),
          );
        }
        return frames;
      });
      assert(
        motion.every((delta) => delta < 1),
        "brand and sidebar must move together",
      );
      const geometry = await page.evaluate(() => {
        const brand = document.querySelector(".brand-area"),
          sidebar = document.querySelector("#sidebar"),
          toggle = document.querySelector("#sidebar-toggle"),
          header = document.querySelector("header"),
          b = brand.getBoundingClientRect(),
          t = toggle.getBoundingClientRect();
        return {
          width: b.width,
          sidebar_width: sidebar.getBoundingClientRect().width,
          header_height: header.getBoundingClientRect().height,
          toggle_inside:
            t.left >= b.left &&
            t.right <= b.right &&
            t.top >= b.top &&
            t.bottom <= b.bottom,
          background: getComputedStyle(header).backgroundColor,
          sidebar_background: getComputedStyle(sidebar).backgroundColor,
        };
      });
      assert.equal(geometry.width, state === "rail" ? 72 : 224);
      assert.equal(geometry.width, geometry.sidebar_width);
      assert.equal(geometry.header_height, beforeHeight);
      assert.equal(geometry.background, geometry.sidebar_background);
      assert(geometry.toggle_inside);
      await capture(page, `native-brand-${state}`);
      result.checks.push({ state: "PASS", layout: state, ...geometry });
    }
    for (const [width, height] of [
      [1180, 820],
      [760, 560],
    ]) {
      await desktop.evaluate(
        ({ BrowserWindow }, bounds) => {
          const window = BrowserWindow.getAllWindows()[0];
          window.setBounds(bounds);
          window.webContents.setZoomFactor(2);
        },
        { width, height },
      );
      await page.waitForFunction(
        (expected) => innerWidth === expected,
        width / 2,
      );
      await page
        .getByRole("button", { name: "问答与结果", exact: true })
        .click();
      await page.locator("#agent-question").focus();
      await capture(page, `native-${width}-zoom200-question`);
      const execute = page.getByRole("button", {
        name: "执行 Hermes 问答测试",
        exact: true,
      });
      await execute.focus();
      await execute.scrollIntoViewIfNeeded();
      await page.waitForFunction(() => {
        const button = document.querySelector(
            '.agent-test button[type="submit"]',
          ),
          rect = button.getBoundingClientRect(),
          x = rect.left + rect.width / 2,
          y = rect.top + rect.height / 2;
        return (
          x >= 0 &&
          x < innerWidth &&
          y >= 0 &&
          y < innerHeight &&
          button.contains(document.elementFromPoint(x, y)) &&
          document.documentElement.scrollWidth === innerWidth
        );
      });
      await capture(page, `native-${width}-zoom200-execute`);
      result.checks.push({
        state: "PASS",
        width,
        height,
        native_zoom_factor: 2,
        css_width: await page.evaluate(() => innerWidth),
        execute_reachable: true,
      });
    }
    assert.deepEqual(result.page_errors, []);
    assert.equal(
      result.requests.filter(
        (r) => r.method === "POST" && r.path.endsWith("/operations"),
      ).length,
      0,
    );
    result.state = "PASS";
  } catch (error) {
    result.state = "FAIL";
    result.error = error.stack;
    process.exitCode = 1;
  } finally {
    await desktop?.close();
    write("native-reflow", result);
    console.log(
      JSON.stringify({ state: result.state, checks: result.checks.length }),
    );
  }
})();
