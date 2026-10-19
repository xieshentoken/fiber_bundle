/* global process, URL, document, window, getComputedStyle, Event, Storage, innerWidth */
// Synthetic fixture only. This test never connects to the user's deployed instances.
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const assert = require("node:assert/strict"),
  fs = require("node:fs"),
  path = require("node:path");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out);
const url = new URL(base);
assert(
  url.hostname === "127.0.0.1" && +url.port >= 18000 && +url.port <= 19000,
);
fs.mkdirSync(out, { recursive: true });
const result = {
  synthetic: true,
  real_models: false,
  browser_plugin: "absent",
  checks: [],
  errors: [],
  console: [],
  pages: [],
  screenshots: [],
};
let browser, page;
(async () => {
  browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1180, height: 820 },
  });
  page = await context.newPage();
  page.on("pageerror", (e) => result.errors.push(e.message));
  page.on("console", (m) => {
    if (["error", "warning"].includes(m.type())) result.console.push(m.text());
  });
  let requests = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/admin/"))
      requests.push(r.method() + " " + new URL(r.url()).pathname);
  });
  page.on("dialog", (d) => d.dismiss());
  const rootTheme = () => page.locator("html").getAttribute("data-theme");
  async function settle() {
    await page.locator("#page-loading").waitFor({ state: "hidden" });
    await page.waitForFunction(
      () => document.documentElement.dataset.fontReady === "true",
    );
  }
  async function shot(name) {
    await page.screenshot({
      path: path.join(out, name + ".png"),
      fullPage: false,
      animations: "disabled",
    });
    result.screenshots.push(name + ".png");
  }
  async function select(id, opener = "#theme-toggle", makeDefault = false) {
    await page.locator(opener).click();
    await page.locator(`[data-theme-choice="${id}"]`).click();
    if (makeDefault && !(await page.locator("#theme-set-default").isDisabled()))
      await page.locator("#theme-set-default").click();
    await page.keyboard.press("Escape");
    await page.locator("#theme-picker").waitFor({ state: "hidden" });
    assert.equal(await rootTheme(), id);
    await settle();
  }
  async function move(id) {
    if (
      (await page.locator(`[data-tab="${id}"]`).getAttribute("hidden")) !== null
    ) {
      await move("worklist");
      await page
        .locator(`#worklist-panel [data-page-target="${id}"]`)
        .first()
        .click();
    } else {
      if (
        (await page.locator("body").getAttribute("data-sidebar")) ===
          "drawer" &&
        (await page
          .locator("#sidebar-toggle")
          .getAttribute("aria-expanded")) !== "true"
      )
        await page.locator("#sidebar-toggle").click();
      await page.locator(`[data-tab="${id}"]`).click();
    }
    await settle();
  }
  await page.goto(base);
  await page.locator("#login-panel").waitFor({ state: "visible" });
  await settle();
  assert.equal(page.url(), base + "/");
  assert.match(await page.title(), /Fiber bundle/);
  assert((await page.locator("body").innerText()).length > 40);
  assert.equal(
    await page.locator("vite-error-overlay,nextjs-portal").count(),
    0,
  );
  await page.locator("#theme-toggle").click();
  assert.equal(await page.locator("#theme-picker details").count(), 0);
  assert.equal(
    await page.evaluate(() => document.activeElement.dataset.themeChoice),
    "clay",
  );
  await shot("theme-picker-login");
  await page.keyboard.press("Escape");
  assert.equal(
    await page.evaluate(() => document.activeElement.id),
    "theme-toggle",
  );
  await select("cursor", "#theme-toggle", true);
  await page.reload();
  await settle();
  assert.equal(await rootTheme(), "cursor");
  result.checks.push("login-logo-theme-persistence-escape-focus");
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button[type=submit]").click();
  await page.locator("#workspace").waitFor({ state: "visible" });
  await settle();
  await page
    .locator("#home-content")
    .getByText("UTC · 全部云凭据", { exact: false })
    .waitFor();
  for (const id of ["oracle", "tesla", "cursor", "clay", "dark"]) {
    await select(id, "#theme-toggle", true);
    await page.reload();
    await settle();
    assert.equal(await rootTheme(), id, "saved theme survives reload");
    await page
      .locator("#home-content")
      .getByText("UTC · 全部云凭据", { exact: false })
      .waitFor();
    if (id === "clay") {
      const card = page.locator("#home-content .ui-card:visible").first();
      await card.waitFor({ state: "visible" });
      await card.scrollIntoViewIfNeeded();
      const rect = await card.boundingBox();
      await page.mouse.move(rect.x + 12, rect.y + 12);
      assert.deepEqual(
        await card.boundingBox(),
        rect,
        "container never moves under pointer",
      );
      await page.emulateMedia({ reducedMotion: "reduce" });
      assert.deepEqual(await card.boundingBox(), rect);
      await page.emulateMedia({ reducedMotion: "no-preference" });
      await card.locator("button").first().focus();
      assert.deepEqual(
        await card.boundingBox(),
        rect,
        "container stays fixed during focus",
      );
      assert.notEqual(
        await card
          .locator("button")
          .first()
          .evaluate((n) => getComputedStyle(n).outlineStyle),
        "none",
      );
      result.checks.push(
        "clay-stationary-container-keyboard-focus-and-reduced-motion",
      );
    }
    await shot("home-" + id);
    for (const entry of [
      "vaults",
      "imports",
      "inventory",
      "graph",
      "worklist",
      "pending",
      "batches",
      "documents",
      "media",
      "creation",
      "channels",
      "clients",
      "models",
      "execution",
      "backend",
      "backups",
      "settings",
    ]) {
      await move(entry);
      assert.equal(await rootTheme(), id);
      assert.equal(
        await page
          .locator(`[data-tab="${entry}"]`)
          .getAttribute("aria-current"),
        "page",
      );
      const metrics = await page.evaluate(() => ({
        width: innerWidth,
        scroll: document.documentElement.scrollWidth,
        text: document.querySelector("#workspace > section:not([hidden])")
          .innerText.length,
      }));
      assert(metrics.text > 10);
      assert(
        metrics.scroll <= metrics.width + 1,
        `${id}/${entry} horizontal overflow`,
      );
      result.pages.push({ theme: id, page: entry, ...metrics });
    }
    await move("home");
  }
  result.checks.push("five-themes-all-seventeen-feature-pages");
  await page.keyboard.press("Meta+k");
  await page.locator("#command-palette").waitFor({ state: "visible" });
  await page.keyboard.press("Escape");
  await page.locator("#command-palette").waitFor({ state: "hidden" });
  result.checks.push("command-palette-shortcut-and-escape");
  await move("settings");
  // UI font changes use native select events; enhanced controls observe the same source.
  await page.locator('[name="latinSize"]').selectOption("18");
  const count = requests.length;
  await select("oracle", "#appearance-theme-change");
  assert.equal(await page.locator('[name="latinSize"]').inputValue(), "18");
  assert.equal(
    await page.evaluate(
      () => JSON.parse(localStorage.getItem("fiber.appearance.v1")).latinSize,
    ),
    14,
  );
  assert.equal(
    requests.length,
    count,
    "theme switch must not reload business data",
  );
  await page.locator('[data-tab="home"]').click();
  assert(
    await page.locator("#settings-panel").isVisible(),
    "dirty settings still block navigation",
  );
  await page.locator("#appearance-cancel").click();
  assert.equal(await rootTheme(), "oracle");
  assert.equal(await page.locator('[name="latinSize"]').inputValue(), "14");
  await page.locator('[name="cjkFont"]').selectOption("theme");
  await page.locator('[name="latinFont"]').selectOption("theme");
  await page.locator("#appearance-form button[type=submit]").click();
  await settle();
  assert.match(
    await page
      .locator("h1")
      .first()
      .evaluate((n) => getComputedStyle(n).fontFamily),
    /Georgia/,
  );
  await select("tesla");
  assert(
    !/Georgia/.test(
      await page
        .locator("h1")
        .first()
        .evaluate((n) => getComputedStyle(n).fontFamily),
    ),
  );
  await page.locator("#appearance-reset").click();
  assert.equal(await rootTheme(), "tesla");
  await page.locator("#appearance-cancel").click();
  result.checks.push(
    "settings-preview-save-cancel-reset-follow-theme-and-no-requests",
  );
  // Cross-window preference synchronization preserves this window's unsaved size.
  const second = await context.newPage();
  await second.goto(base);
  await second.waitForFunction(
    () => document.documentElement.dataset.fontReady === "true",
  );
  await page.locator('[name="cjkSize"]').selectOption("19");
  await second.locator("#theme-toggle").click();
  await second.locator('[data-theme-choice="cursor"]').click();
  await second.locator("#theme-set-default").click();
  await page.waitForFunction(
    () =>
      JSON.parse(localStorage.getItem("fiber.appearance.v1")).defaultTheme ===
      "cursor",
  );
  assert.equal(await rootTheme(), "tesla");
  assert.equal(await page.locator('[name="cjkSize"]').inputValue(), "19");
  await page.locator("#appearance-cancel").click();
  await second.close();
  result.checks.push("cross-window-saved-theme-preserves-local-preview");
  // Denied storage: same theme/dirty fields, visible error, safe retry.
  await page.locator('[name="latinSize"]').selectOption("17");
  await page.evaluate(() => {
    window.__themeSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === "fiber.appearance.v1")
        throw new Error("synthetic denied storage");
      return window.__themeSetItem.call(this, key, value);
    };
  });
  await page.locator("#theme-toggle").click();
  await page.locator('[data-theme-choice="oracle"]').click();
  await page.locator("#theme-set-default").click();
  assert(await page.locator(".theme-picker-error").isVisible());
  assert.equal(await rootTheme(), "oracle");
  assert.equal(await page.locator('[name="latinSize"]').inputValue(), "17");
  await shot("theme-storage-error");
  await page.evaluate(() => {
    Storage.prototype.setItem = window.__themeSetItem;
  });
  await page.locator("#theme-set-default").click();
  await page.keyboard.press("Escape");
  await page.locator("#appearance-cancel").click();
  result.checks.push("storage-error-keeps-theme-and-draft-retry-succeeds");
  // Modal keyboard focus stays contained; opening/hovering/Esc does not commit.
  await page.locator("#theme-toggle").click();
  for (let i = 0; i < 14; i++) {
    await page.keyboard.press("Tab");
    assert(
      await page.evaluate(
        () => !!document.activeElement.closest("#theme-picker"),
      ),
      `Tab ${i}: ${await page.evaluate(() => document.activeElement.outerHTML)}`,
    );
  }
  await page.keyboard.press("Escape");
  assert.equal(await rootTheme(), "oracle");
  await page.locator("#theme-toggle").click();
  await page.locator('[data-theme-choice="oracle"]').press("Enter");
  assert(await page.locator("#theme-picker").isVisible());
  await page.keyboard.press("Escape");
  await page.locator("#theme-picker").waitFor({ state: "hidden" });
  await page.locator("#theme-toggle").click();
  await page.locator('[data-theme-choice="oracle"]').press("Space");
  assert(await page.locator("#theme-picker").isVisible());
  await page.keyboard.press("Escape");
  await page.locator("#theme-picker").waitFor({ state: "hidden" });
  await page.locator("#theme-toggle").click();
  await page.mouse.click(1, 1);
  await page.locator("#theme-picker").waitFor({ state: "hidden" });
  assert.equal(await rootTheme(), "oracle");
  result.checks.push("keyboard-enter-space-and-backdrop-cancel");
  // Rail logo stays reachable. Repeated current selection must not rewrite storage.
  await page.locator("#sidebar-toggle").click();
  await select("tesla");
  await page.locator("#sidebar-toggle").click();
  await move("home");
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    for (const id of ["oracle", "tesla", "cursor", "clay", "dark"]) {
      await select(id);
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      );
      await page.locator("#theme-toggle").click();
      assert(await page.locator(`[data-theme-choice="${id}"]`).isVisible());
      await shot(`picker-${id}-${width}`);
      await page.keyboard.press("Escape");
    }
  }
  await page.setViewportSize({ width: 1180, height: 820 });
  // Reflow at the equivalent 200% viewport and independent largest font sizes.
  await page.setViewportSize({ width: 590, height: 410 });
  await select("oracle");
  await shot("reflow-200-equivalent");
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  );
  result.checks.push("keyboard-modal-rail-mobile-320-390-1440-reflow");
  // An unauthorized response while the dialog is open closes it during signedOut.
  await page.locator("#theme-toggle").click();
  // Use the actual authenticated request flow, with a controlled 401 response.
  await page.route("**/admin/session", (r) =>
    r.fulfill({ status: 401, json: { error: "synthetic expired" } }),
  );
  await page.evaluate(() => document.querySelector("#refresh").click());
  await page.locator("#login-panel").waitFor({ state: "visible" });
  await page.locator("#theme-picker").waitFor({ state: "hidden" });
  result.checks.push("session-expiry-closes-theme-dialog");
  assert.deepEqual(result.errors, []);
  // A controlled 401 response is expected; record all console entries for inspection.
  result.pass = true;
  await browser.close();
})()
  .catch(async (error) => {
    result.pass = false;
    result.failure = error.stack;
    if (page) {
      await page.screenshot({ path: path.join(out, "failure.png") });
      fs.writeFileSync(
        path.join(out, "failure.txt"),
        await page.locator("body").innerText(),
      );
    }
    if (browser) await browser.close();
    process.exitCode = 1;
  })
  .finally(() => {
    fs.writeFileSync(
      path.join(out, "themes-results.json"),
      JSON.stringify(result, null, 2) + "\n",
    );
    console.log(
      JSON.stringify({
        pass: result.pass,
        checks: result.checks,
        pages: result.pages.length,
        errors: result.errors,
        failure: result.failure,
      }),
    );
  });
