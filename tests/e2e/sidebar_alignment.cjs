// Opt-in native layout regression against an isolated server and fresh profile.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { _electron } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const base = new URL(process.env.FIBER_GUI_ACCEPTANCE_URL);
assert.equal(base.hostname, "127.0.0.1");
assert(Number(base.port) >= 18000 && Number(base.port) <= 19000);
const output = process.env.FIBER_GUI_EVIDENCE;
assert(output && !fs.existsSync(output), "Use a fresh evidence directory");
fs.mkdirSync(output, { recursive: true, mode: 0o700 });
const app = process.env.FIBER_GUI_ELECTRON_APP;
assert(app, "Set the exact packaged Electron executable");
const report = { state: "RUNNING", checks: [], page_errors: [], requests: [] };
let desktop;

async function capture(page, name) {
  await page.evaluate(() => document.fonts.ready);
  const encoded = await desktop.evaluate(async ({ BrowserWindow }) =>
    (await BrowserWindow.getAllWindows()[0].webContents.capturePage())
      .toPNG()
      .toString("base64"),
  );
  fs.writeFileSync(
    path.join(output, name + ".png"),
    Buffer.from(encoded, "base64"),
  );
}

async function layout(page, state) {
  const height = await page.locator("header").evaluate((n) => n.offsetHeight);
  if ((await page.evaluate(() => document.body.dataset.sidebar)) !== state)
    await page.locator("#sidebar-toggle").click();
  const motion = await page.evaluate(async () => {
    const deltas = [],
      end = performance.now() + 350;
    while (performance.now() < end) {
      await new Promise(requestAnimationFrame);
      deltas.push(
        Math.abs(
          document.querySelector(".brand-area").getBoundingClientRect().right -
            document.querySelector("#sidebar").getBoundingClientRect().right,
        ),
      );
    }
    return deltas;
  });
  assert(
    motion.every((delta) => delta < 1),
    "Brand and sidebar must animate together",
  );
  const geometry = await page.evaluate(() => {
    const brand = document.querySelector(".brand-area").getBoundingClientRect(),
      toggle = document
        .querySelector("#sidebar-toggle")
        .getBoundingClientRect(),
      sidebar = document.querySelector("#sidebar"),
      header = document.querySelector("header");
    return {
      width: brand.width,
      sidebar_width: sidebar.getBoundingClientRect().width,
      header_height: header.offsetHeight,
      background: getComputedStyle(header).backgroundColor,
      sidebar_background: getComputedStyle(sidebar).backgroundColor,
      toggle_width: toggle.width,
      toggle_height: toggle.height,
      toggle_inside:
        toggle.left >= brand.left &&
        toggle.right <= brand.right &&
        toggle.top >= brand.top &&
        toggle.bottom <= brand.bottom,
      overflow: document.documentElement.scrollWidth > innerWidth,
    };
  });
  assert.equal(geometry.width, state === "rail" ? 72 : 224);
  assert.equal(geometry.sidebar_width, geometry.width);
  assert.equal(geometry.header_height, height);
  assert.equal(geometry.background, geometry.sidebar_background);
  assert.equal(geometry.toggle_width, 44);
  assert.equal(geometry.toggle_height, 44);
  assert(geometry.toggle_inside);
  assert.equal(geometry.overflow, false);
  report.checks.push({ state: "PASS", layout: state, ...geometry });
}

(async () => {
  try {
    desktop = await _electron.launch({
      executablePath: app,
      args: ["--url", base.href, "--user-data", path.join(output, "profile")],
      timeout: 25000,
    });
    const page = await desktop.firstWindow();
    page.on("pageerror", (error) => report.page_errors.push(error.message));
    page.on("response", (r) =>
      report.requests.push({
        method: r.request().method(),
        path: new URL(r.url()).pathname,
        status: r.status(),
      }),
    );
    assert.equal(new URL(page.url()).origin, base.origin);
    await page.locator("#password").fill("synthetic-gui-acceptance");
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator("#workspace").waitFor({ state: "visible" });
    await page.locator("#vault-list .row").first().waitFor();
    await page.evaluate(() => document.fonts.ready);
    for (const state of ["rail", "expanded"]) {
      await layout(page, state);
      await capture(page, state);
    }
    await page.emulateMedia({ reducedMotion: "reduce" });
    for (const state of ["rail", "expanded"]) await layout(page, state);
    for (const [width, height, zoom] of [
      [760, 560, 1],
      [1180, 820, 2],
    ]) {
      await desktop.evaluate(
        ({ BrowserWindow }, { width, height, zoom }) => {
          const w = BrowserWindow.getAllWindows()[0];
          w.setBounds({ width, height });
          w.webContents.setZoomFactor(zoom);
        },
        { width, height, zoom },
      );
      await page.waitForFunction(
        () => document.body.dataset.sidebar === "drawer",
      );
      await page.locator("#sidebar-toggle").click();
      assert.equal(
        await page.locator("#sidebar-toggle").getAttribute("aria-expanded"),
        "true",
      );
      await page.keyboard.press("Escape");
      assert.equal(
        await page.locator("#sidebar-toggle").getAttribute("aria-expanded"),
        "false",
      );
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
      );
      await capture(page, `drawer-${width}-${zoom}`);
      report.checks.push({
        state: "PASS",
        width,
        height,
        native_zoom: zoom,
        drawer_keyboard: "PASS",
      });
    }
    assert.deepEqual(report.page_errors, []);
    assert(
      report.requests.every(
        (r) => r.method !== "POST" || r.path === "/admin/login",
      ),
    );
    report.state = "PASS";
  } catch (error) {
    report.state = "FAIL";
    report.error = error.stack;
    process.exitCode = 1;
  } finally {
    await desktop?.close();
    fs.writeFileSync(
      path.join(output, "sidebar-alignment.json"),
      JSON.stringify(report, null, 2) + "\n",
    );
    console.log(
      JSON.stringify({ state: report.state, checks: report.checks.length }),
    );
  }
})();
