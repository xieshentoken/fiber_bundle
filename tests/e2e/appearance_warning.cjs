/* global process, window, document, localStorage, Storage */
// An isolated older desktop main plus the new preload reproduces an in-place GUI upgrade.
const { _electron } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const assert = require("node:assert/strict"),
  fs = require("node:fs"),
  path = require("node:path");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out && process.env.FIBER_OLD_DESKTOP_ENTRY);
const url = new URL(base);
assert(
  url.hostname === "127.0.0.1" && +url.port >= 18000 && +url.port <= 19000,
);
fs.mkdirSync(out, { recursive: true });
const result = {
  synthetic: true,
  old_desktop_main: true,
  checks: [],
  errors: [],
  diagnostics: [],
};
let app, page;
(async () => {
  app = await _electron.launch({
    executablePath:
      process.env.FIBER_ELECTRON_EXECUTABLE ||
      path.resolve(
        __dirname,
        "../../node_modules/electron/dist/Electron.app/Contents/MacOS/Electron",
      ),
    args: [
      process.env.FIBER_OLD_DESKTOP_ENTRY,
      "--url",
      base,
      "--user-data",
      path.join(out, "profile"),
    ],
  });
  page = await app.firstWindow();
  page.on("pageerror", (e) => result.errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "warning") result.diagnostics.push(m.text());
  });
  page.on("dialog", (d) => d.dismiss());
  await page.locator("#login-panel").waitFor({ state: "visible" });
  // Startup may log before the listener attaches; count actual attempts from the main error logs below through fresh reload.
  await page.reload();
  await page.locator("#login-panel").waitFor({ state: "visible" });
  await page.waitForFunction(
    () => document.documentElement.dataset.fontReady === "true",
  );
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button").click();
  await page.locator("#workspace").waitFor({ state: "visible" });
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  const initial = result.diagnostics.length;
  for (const id of [
    "vaults",
    "inventory",
    "graph",
    "channels",
    "execution",
    "settings",
    "home",
    "inventory",
  ]) {
    await page.locator(`[data-tab="${id}"]`).click();
    await page.locator("#page-loading").waitFor({ state: "hidden" });
    assert(
      !/桌面启动主题|外观.*同步/.test(
        await page.locator("#notice").innerText(),
      ),
    );
    assert.equal(
      result.diagnostics.length,
      initial,
      "navigation must not repeat failed desktop sync",
    );
  }
  assert(
    result.diagnostics.some((x) =>
      x.includes("Desktop appearance sync unavailable"),
    ),
  );
  result.checks.push(
    "old-main-new-preload-missing-handler-is-diagnostic-only; eight sidebar transitions no banner or retry",
  );
  await page.locator("#theme-toggle").click();
  await page.locator('[data-theme-choice="dark"]').click();
  await page.locator("#theme-set-default").click();
  assert.equal(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("fiber.appearance.v1")).defaultTheme,
    ),
    "dark",
  );
  await page.keyboard.press("Escape");
  await page.reload();
  await page.locator("#workspace").waitFor({ state: "visible" });
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  result.checks.push("web theme default persists despite old native main");
  await page.locator("#theme-toggle").click();
  await page.locator('[data-theme-choice="tesla"]').click();
  await page.evaluate(() => {
    Storage.prototype.setItem = () => {
      throw Error("synthetic denied");
    };
  });
  await page.locator("#theme-set-default").click();
  assert(await page.locator(".theme-picker-error").isVisible());
  assert.equal(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("fiber.appearance.v1")).defaultTheme,
    ),
    "dark",
  );
  result.checks.push(
    "real browser persistence failure remains visible in theme dialog",
  );
  await page.keyboard.press("Escape");
  await page.locator('[data-tab="inventory"]').click();
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page.screenshot({
    path: path.join(out, "inventory-no-warning.png"),
    fullPage: false,
  });
  assert.deepEqual(result.errors, []);
  result.pass = true;
})()
  .catch((e) => {
    result.pass = false;
    result.failure = e.stack;
    process.exitCode = 1;
  })
  .finally(async () => {
    await app?.close();
    fs.writeFileSync(
      path.join(out, "results.json"),
      JSON.stringify(result, null, 2),
    );
    console.log(JSON.stringify(result));
  });
