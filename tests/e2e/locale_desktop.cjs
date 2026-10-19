// Actual Electron with synthetic backend and a fresh isolated profile only.
const { _electron } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict"),
  { pathToFileURL } = require("node:url");
const root = path.resolve(__dirname, "../.."),
  base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out);
const url = new URL(base);
assert(
  url.hostname === "127.0.0.1" && +url.port >= 18000 && +url.port <= 19000,
);
fs.mkdirSync(out, { recursive: true });
const profile = path.join(out, "locale-profile");
assert(!fs.existsSync(profile), "Use a fresh isolated profile");
let app;
(async () => {
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
  });
  const page = await app.firstWindow(),
    errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.locator("#login-panel").waitFor({ state: "visible" });
  await page
    .locator("[data-language-control]")
    .first()
    .selectOption("en", { force: true });
  await page.waitForFunction(
    async () => (await window.fiberDesktop.localeSnapshot()).language === "en",
  );
  const menu = await app.evaluate(({ Menu }) =>
    Menu.getApplicationMenu().items.map((x) => ({
      label: x.label,
      children: x.submenu?.items.map((y) => y.label),
    })),
  );
  assert(menu.some((x) => x.label === "View"));
  assert(menu.some((x) => x.children?.includes("Reconnect Backend")));
  const security = await app.evaluate(({ BrowserWindow }) => {
    const p =
      BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences();
    return [p.sandbox, p.contextIsolation, p.nodeIntegration];
  });
  assert.deepEqual(security, [true, true, false]);
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(2),
  );
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
    "200% native zoom reflows",
  );
  await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(1),
  );
  const nativeDir =
    process.env.FIBER_DESKTOP_ENTRY || path.join(root, "desktop");
  await app.evaluate(
    async ({ BrowserWindow }, url) =>
      BrowserWindow.getAllWindows()[0].loadURL(url),
    pathToFileURL(path.join(nativeDir, "offline.html")).href,
  );
  await page.waitForFunction(() => document.documentElement.lang === "en");
  assert.equal(
    await page.locator("h1").innerText(),
    "Cannot connect to the local backend",
  );
  assert.match(await page.locator("#offline-retry").innerText(), /seconds/);
  assert.equal(
    await page.evaluate(async () => {
      try {
        await window.fiberDesktop.applyLocale({ language: "zh-CN" });
        return false;
      } catch {
        return true;
      }
    }),
    true,
    "offline write rejected",
  );
  assert.equal(
    await page.evaluate(async () => {
      try {
        await window.fiberDesktop.selectDirectory();
        return false;
      } catch {
        return true;
      }
    }),
    true,
    "offline business IPC rejected",
  );
  await page.screenshot({ path: path.join(out, "native-offline-en.png") });
  await app.evaluate(
    async ({ BrowserWindow }, url) =>
      BrowserWindow.getAllWindows()[0].loadURL(url),
    base,
  );
  await page.locator("#login-panel").waitFor({ state: "visible" });
  assert.equal(await page.locator("html").getAttribute("lang"), "en");
  assert.deepEqual(errors, []);
  fs.writeFileSync(
    path.join(out, "native-locale-results.json"),
    JSON.stringify(
      {
        pass: true,
        synthetic: true,
        actual_electron: true,
        menu,
        security,
        errors,
        checks: [
          "English menu after explicit selection",
          "English offline copy and timer",
          "offline preference writes and business IPC rejected",
          "web language retained after reconnect",
        ],
      },
      null,
      2,
    ),
  );
  await app.close();
  app = null;
  console.log("Native locale PASS");
})().catch(async (e) => {
  console.error(e);
  if (app) await app.close();
  process.exit(1);
});
