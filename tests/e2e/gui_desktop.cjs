// Actual Electron; explicitly isolated profile/backend. Never launch a user's installed app.
const { _electron } = require("playwright"),
  assert = require("node:assert/strict"),
  fs = require("node:fs"),
  path = require("node:path"),
  net = require("node:net");
const root = path.resolve(__dirname, "../.."),
  base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out);
const u = new URL(base);
assert(u.hostname === "127.0.0.1" && +u.port >= 18000 && +u.port <= 19000);
fs.mkdirSync(out, { recursive: true });
const packaged = process.env.FIBER_GUI_ELECTRON_EXECUTABLE;
const executable =
  packaged ||
  path.join(
    root,
    "node_modules/electron/dist/Electron.app/Contents/MacOS/Electron",
  );
let app;
async function launch(url, profile) {
  app = await _electron.launch({
    executablePath: executable,
    args: [
      ...(packaged ? [] : [path.join(root, "desktop")]),
      "--url",
      url,
      "--user-data",
      profile,
    ],
    timeout: 30000,
  });
  return app.firstWindow();
}
(async () => {
  const profile = path.join(out, "profile"),
    errors = [];
  let page = await launch(base, profile);
  page.on("pageerror", (e) => errors.push(e.message));
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button").click();
  await page.locator("#home-cost-title").waitFor();
  for (const theme of ["cursor", "oracle", "tesla"]) {
    await page.locator("#theme-toggle").click();
    await page.locator(`[data-theme-choice="${theme}"]`).click();
    await page.waitForFunction(
      (t) => document.documentElement.dataset.theme === t,
      theme,
    );
    await page.screenshot({ path: out + `/electron-${theme}.png` });
    for (const width of [1180, 760]) {
      await app.evaluate(({ BrowserWindow }, width) => {
        const w = BrowserWindow.getAllWindows()[0];
        w.setBounds({ width, height: 760 });
        w.webContents.setZoomFactor(2);
      }, width);
      await page.waitForFunction((width) => innerWidth <= width / 2 + 1, width);
      await page.evaluate(async () => {
        await document.fonts.ready;
        await new Promise(requestAnimationFrame);
      });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
        `native 200% overflow: ${theme}/${width}`,
      );
      const png = await app.evaluate(async ({ BrowserWindow }) =>
        (await BrowserWindow.getAllWindows()[0].webContents.capturePage())
          .toPNG()
          .toString("base64"),
      );
      fs.writeFileSync(
        out + `/zoom200-${theme}-${width}.png`,
        Buffer.from(png, "base64"),
      );
      await app.evaluate(({ BrowserWindow }) =>
        BrowserWindow.getAllWindows()[0].webContents.setZoomFactor(1),
      );
    }
  }
  const checked = await app.evaluate(({ BrowserWindow, Menu, screen }) => {
    const w = BrowserWindow.getAllWindows()[0];
    const area = screen.getPrimaryDisplay().workArea;
    w.setBounds({
      x: area.x + 32,
      y: area.y + 32,
      width: Math.max(760, Math.min(1080, area.width - 64)),
      height: Math.max(560, Math.min(760, area.height - 64)),
    });
    const prefs = w.webContents.getLastWebPreferences(),
      menu = Menu.getApplicationMenu();
    return {
      bounds: w.getNormalBounds(),
      sandbox: prefs.sandbox,
      isolated: prefs.contextIsolation,
      node: prefs.nodeIntegration,
      items: menu.items.flatMap(
        (i) =>
          i.submenu?.items.map((j) => ({
            role: j.role,
            accelerator: j.accelerator,
          })) || [],
      ),
    };
  });
  assert(checked.sandbox && checked.isolated && !checked.node);
  assert(checked.items.some((i) => i.role === "hide"));
  assert(checked.items.some((i) => i.accelerator === "CmdOrCtrl+Q"));
  await page.evaluate(() => new Promise(requestAnimationFrame));
  checked.bounds = await app.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows()[0].getNormalBounds(),
  );
  await app.close();
  app = null;
  const saved = JSON.parse(
    fs.readFileSync(path.join(profile, "window-state.json"), "utf8"),
  );
  assert.deepEqual(saved, checked.bounds);
  page = await launch(base, profile);
  await page.locator("#home-cost-title").waitFor();
  assert.equal(
    await page.evaluate(() => document.documentElement.dataset.theme),
    "tesla",
  );
  assert.deepEqual(
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].getNormalBounds(),
    ),
    saved,
  );
  await app.evaluate(async ({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows()[0];
    await new Promise((resolve) => {
      w.once("closed", resolve);
      w.close();
    });
  });
  assert.equal(
    await app.evaluate(
      ({ BrowserWindow }) => BrowserWindow.getAllWindows().length,
    ),
    0,
  );
  assert.equal((await fetch(base + "/health")).status, 200);
  await app.evaluate(({ app }) => app.emit("activate"));
  page = await app.firstWindow();
  await page.locator("#home-cost-title").waitFor();
  await app.evaluate(({ app }) => app.emit("second-instance"));
  assert.equal(
    await app.evaluate(
      ({ BrowserWindow }) => BrowserWindow.getAllWindows().length,
    ),
    1,
  );
  await app.close();
  app = null;
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  page = await launch(
    "http://127.0.0.1:" + port + "/",
    path.join(out, "offline-profile"),
  );
  await page.getByText("无法连接本机后台", { exact: true }).waitFor();
  assert(await page.locator("#offline-retry").isVisible());
  assert(
    (await page.locator("#offline-retry").innerText()).includes("重新检查"),
  );
  await page.screenshot({ path: out + "/offline.png" });
  await app.close();
  app = null;
  assert.deepEqual(errors, []);
  fs.writeFileSync(
    out + "/result.json",
    JSON.stringify(
      {
        actual_electron: true,
        synthetic: true,
        three_theme: true,
        native_zoom_200: true,
        geometry_roundtrip: saved,
        window_close_backend_alive: true,
        reopen_single_window: true,
        offline: true,
        security_preferences: true,
        errors,
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS: actual Electron themes, geometry, menu, close/reopen, backend independence, offline",
  );
})().catch(async (e) => {
  console.error(e);
  if (app) await app.close();
  process.exitCode = 1;
});
