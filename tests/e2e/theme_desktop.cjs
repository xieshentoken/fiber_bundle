/* global process, URL, document, window, getComputedStyle */
// Actual macOS Electron shell, isolated synthetic backend and fresh local profile.
const { _electron } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const fs = require("node:fs"),
  path = require("node:path"),
  { pathToFileURL } = require("node:url"),
  assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../.."),
  base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out);
const url = new URL(base);
assert(
  url.hostname === "127.0.0.1" && +url.port >= 18000 && +url.port <= 19000,
);
fs.mkdirSync(out, { recursive: true });
const result = {
  synthetic: true,
  actual_electron: true,
  checks: [],
  errors: [],
};
let app, page;
const profile = path.join(out, "profile");
assert(!fs.existsSync(profile), "Use a fresh isolated Electron profile");
async function launch() {
  app = await _electron.launch({
    executablePath:
      process.env.FIBER_ELECTRON_EXECUTABLE ||
      path.join(
        root,
        "node_modules/electron/dist/Electron.app/Contents/MacOS/Electron",
      ),
    args: [
      process.env.FIBER_DESKTOP_ENTRY || path.join(root, "desktop"),
      "--url",
      base,
      "--user-data",
      profile,
    ],
    timeout: 25000,
  });
  page = await app.firstWindow();
  page.on("pageerror", (e) => result.errors.push(e.message));
  await page.waitForFunction(
    () => document.documentElement.dataset.fontReady === "true",
  );
}
async function select(id, { saveDefault = false } = {}) {
  await page.locator("#theme-toggle").click();
  assert.equal(await page.locator("[data-theme-choice]").count(), 6);
  await page.locator(`[data-theme-choice="${id}"]`).click();
  if (saveDefault) {
    await page.locator("#theme-set-default").click();
    await page.waitForFunction(
      async (id) =>
        (await window.fiberDesktop.appearanceSnapshot()).defaultTheme === id,
      id,
    );
  }
  await page.getByRole("button", { name: "关闭主题选择" }).click();
  await page.locator("#theme-picker").waitFor({ state: "hidden" });
  assert.equal(await page.locator("html").getAttribute("data-theme"), id);
  await page.waitForFunction(
    async (id) => (await window.fiberDesktop.appearanceSnapshot()).theme === id,
    id,
  );
  await page.waitForFunction(
    () => document.documentElement.dataset.fontReady === "true",
  );
  await nativeTheme(id);
}
async function nativeTheme(id) {
  const value = await app.evaluate(({ nativeTheme, BrowserWindow }) => ({
    source: nativeTheme.themeSource,
    dark: nativeTheme.shouldUseDarkColors,
    background: BrowserWindow.getAllWindows()[0].getBackgroundColor(),
  }));
  assert.equal(value.source, id === "dark" ? "dark" : "light");
  assert.equal(value.dark, id === "dark");
  const background = {
    oracle: "fbf9f8",
    tesla: "ffffff",
    cursor: "f7f7f4",
    clay: "f5f5f3",
    dark: "0b0b0a",
  }[id];
  assert.match(value.background, new RegExp(`^#(?:ff)?${background}$`, "i"));
}
async function capture(name) {
  const encoded = await app.evaluate(async ({ BrowserWindow }) =>
    (await BrowserWindow.getAllWindows()[0].webContents.capturePage())
      .toPNG()
      .toString("base64"),
  );
  fs.writeFileSync(
    path.join(out, name + ".png"),
    Buffer.from(encoded, "base64"),
  );
}
(async () => {
  await launch();
  assert(page.url().startsWith(base));
  assert.match(await page.title(), /Fiber bundle/);
  const security = await app.evaluate(({ BrowserWindow }) => {
    const value =
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences();
    return {
      sandbox: value.sandbox,
      contextIsolation: value.contextIsolation,
      nodeIntegration: value.nodeIntegration,
    };
  });
  assert.deepEqual(security, {
    sandbox: true,
    contextIsolation: true,
    nodeIntegration: false,
  });
  await select("cursor");
  await capture("native-login-cursor");
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button").click();
  await page.locator("#workspace").waitFor({ state: "visible" });
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page.locator("#sidebar-toggle").click();
  for (const id of ["oracle", "tesla", "cursor", "clay", "dark"]) {
    await select(id);
    await capture("native-" + id);
  }
  result.checks.push(
    "native-login-authenticated-rail-logo-five-themes-with-native-colors",
  );
  assert.equal(
    await page.evaluate(
      async () => (await window.fiberDesktop.appearanceSnapshot()).defaultTheme,
    ),
    "clay",
  );
  await page.locator('[data-tab="settings"]').click();
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page
    .locator("#font-status")
    .getByText("个本机字体家族", { exact: false })
    .waitFor();
  await page.locator('[name="cjkFont"]').selectOption({ label: "跟随主题" });
  await page.locator('[name="latinFont"]').selectOption({ label: "跟随主题" });
  await page.locator("#appearance-form button[type=submit]").click();
  await page.waitForFunction(
    () => document.documentElement.dataset.fontReady === "true",
  );
  await select("oracle");
  assert.match(
    await page
      .locator("h1")
      .first()
      .evaluate((n) => getComputedStyle(n).fontFamily),
    /Georgia/,
  );
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(2),
  );
  await page.locator("#theme-toggle").click();
  await capture("native-zoom-200-theme-picker");
  assert(await page.locator('[data-theme-choice="oracle"]').isVisible());
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  );
  await page.keyboard.press("Escape");
  assert.equal(
    await page.evaluate(() => document.activeElement.id),
    "theme-toggle",
  );
  result.checks.push(
    "native-installed-fonts-theme-fonts-200-percent-zoom-focus",
  );
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(1),
  );
  await select("dark", { saveDefault: true });
  await select("oracle");
  assert.equal(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("fiber.appearance.v1")).defaultTheme,
    ),
    "dark",
  );
  result.checks.push(
    "native-transient-theme-does-not-overwrite-explicit-default",
  );
  await app.close();
  app = null;
  await launch();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  await nativeTheme("dark");
  assert.equal(
    await page.evaluate(
      () => JSON.parse(localStorage.getItem("fiber.appearance.v1")).cjkFont,
    ),
    "theme",
  );
  result.checks.push("native-profile-restart-default-and-font-persistence");
  await select("clay");
  await page.reload();
  await page.waitForFunction(
    () => document.documentElement.dataset.fontReady === "true",
  );
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  await nativeTheme("dark");
  result.checks.push("native-refresh-restores-default");

  const appPath = await app.evaluate(({ app }) => app.getAppPath());
  const offlineURL = pathToFileURL(path.join(appPath, "offline.html")).href;
  // Fail only this isolated window's backend navigation, then exercise the actual
  // reconnect menu's fallback rather than stopping a shared synthetic server.
  await page.route(url.href, (route) => route.abort("connectionrefused"));
  await app.evaluate(({ Menu }) => {
    Menu.getApplicationMenu()
      .items[0].submenu.items.find((item) => item.label === "重新连接后台")
      .click();
  });
  // The intentionally refused intermediate navigation is expected. Observe the
  // fallback document instead of treating that navigation's failure as the test.
  await page.waitForFunction(
    (target) => window.location.href === target,
    offlineURL,
  );
  await page.waitForFunction(
    () => document.documentElement.dataset.theme === "dark",
  );
  await page.evaluate(() => document.fonts.ready);
  await nativeTheme("dark");
  assert.equal(
    await page
      .locator("html")
      .evaluate((n) => getComputedStyle(n).backgroundColor),
    "rgb(11, 11, 10)",
  );
  const snapshot = await page.evaluate(() =>
    window.fiberDesktop.appearanceSnapshot(),
  );
  assert.deepEqual(snapshot, { theme: "dark", defaultTheme: "dark" });
  const denied = await page.evaluate(async () => {
    try {
      await window.fiberDesktop.applyAppearance({
        theme: "tesla",
        defaultTheme: "tesla",
      });
      return false;
    } catch (error) {
      return String(error).includes("Untrusted appearance request");
    }
  });
  assert(denied, "Offline page must not write native defaults");
  await nativeTheme("dark");
  await capture("native-offline-dark");
  result.checks.push("native-offline-dark-snapshot-and-write-denied");
  await page.unroute(url.href);
  await page.locator('a[href="fiber://reconnect"]').click();
  await page.waitForURL(url.href);
  await page.waitForFunction(
    () => document.documentElement.dataset.fontReady === "true",
  );
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  await nativeTheme("dark");
  await capture("native-reconnected-dark");
  result.checks.push("native-offline-reconnect-retains-default");
  assert.deepEqual(result.errors, []);
  result.pass = true;
  await app.close();
  app = null;
})()
  .catch(async (e) => {
    result.pass = false;
    result.failure = e.stack;
    if (app) {
      await capture("failure");
      await app.close();
    }
    process.exitCode = 1;
  })
  .finally(() => {
    fs.writeFileSync(
      path.join(out, "desktop-results.json"),
      JSON.stringify(result, null, 2),
    );
    console.log(JSON.stringify(result));
  });
