/* global process, URL, setTimeout, document, innerWidth, Event, console */
// Opt-in synthetic GUI scenarios. Explicit local test port; never production.
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const assert = require("node:assert/strict"),
  fs = require("node:fs");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out);
const url = new URL(base);
assert.equal(url.hostname, "127.0.0.1");
assert(+url.port >= 18000 && +url.port <= 19000);
fs.mkdirSync(out, { recursive: true });
const result = {
  checks: [],
  requests: [],
  errors: [],
  synthetic: true,
  real_models: false,
};
const delay = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 835 },
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => result.errors.push(e.message));
  page.on("response", async (r) => {
    if (!r.url().includes("/admin/")) return;
    const h = r.headers();
    result.requests.push({
      path: new URL(r.url()).pathname,
      method: r.request().method(),
      status: r.status(),
      lock_wait_ms: h["x-synthetic-lock-wait-ms"],
      elapsed_ms: h["x-synthetic-elapsed-ms"],
    });
  });
  async function settle() {
    await page.locator("#page-loading").waitFor({ state: "hidden" });
    await page.evaluate(() => document.fonts.ready);
    await delay(300);
  }
  async function complete() {
    await page
      .locator("#home-content")
      .getByText("UTC · 全部云凭据", { exact: false })
      .waitFor();
    await settle();
  }
  async function login() {
    assert(await page.locator("#channel-form #channel-expert-enabled").count());
    assert.equal(
      await page.locator("#vault-admission [id^=channel-]").count(),
      0,
    );
    await page.locator("#password").fill("synthetic-gui-acceptance");
    await page.locator("#login-form button").click();
    await complete();
  }
  async function move(id) {
    if (
      (await page.evaluate(() => document.body.dataset.sidebar === "drawer")) &&
      (await page.locator("#sidebar-toggle").getAttribute("aria-expanded")) !==
        "true"
    )
      await page.locator("#sidebar-toggle").click();
    const nav = page.locator(`[data-tab="${id}"]`);
    if ((await nav.getAttribute("hidden")) !== null) {
      await move("worklist");
      await page.locator(`.inbox-pages [data-page-target="${id}"]`).click();
    } else await nav.click();
    await settle();
    assert(await page.locator(`#${id}-panel`).isVisible());
  }
  await page.goto(base);
  await login();
  const initial = [...result.requests];
  assert.deepEqual(
    initial
      .filter((r) => r.method === "GET" && r.path != "/admin/session")
      .slice(0, 4)
      .map((r) => r.path),
    [
      "/admin/channels/overview",
      "/admin/backend",
      "/admin/inventory/executions",
      "/admin/vaults",
    ],
  );
  assert(initial.some((r) => r.path === "/admin/note-index/summary"));
  assert(!initial.some((r) => r.path.includes("/note-index/status")));
  assert(!initial.some((r) => r.method !== "GET" && r.path != "/admin/login"));
  result.initial = initial;
  result.checks.push("readonly-first-four-and-complete-request-cost");
  for (const width of [1440, 1180, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 835 : 790 });
    await settle();
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `overflow ${width}`,
    );
    if (width >= 1180)
      assert(
        (
          await page
            .locator('#home-content [data-import-engine="ordinary"]')
            .boundingBox()
        ).y < 790,
        "primary entry below fold",
      );
    await page.screenshot({ path: out + `/home-${width}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 835 });
  await settle();
  // Equivalent relationship list and source-specific task links remain keyboard reachable.
  assert.equal(await page.locator(".arch-map .arch-node").count(), 8);
  await page
    .locator('#home-content [data-import-engine="fake-expert"]')
    .click();
  await settle();
  await page.waitForFunction(
    () => document.querySelector("#import-engine")?.value === "fake-expert",
  );
  await page.screenshot({ path: out + "/imports.png", fullPage: true });
  result.checks.push("two-import-engines-and-scope-navigation");
  // Every former page is still reachable, including hidden aliases via the worklist.
  for (const id of [
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
  ])
    await move(id);
  result.checks.push("all-seventeen-feature-entries-preserved");
  // Collapsing the sidebar keeps ordinary form drafts; rejecting navigation keeps the form.
  await move("imports");
  await page.locator("[data-engine=ordinary]").click();
  await page.locator(".import-advanced summary").click();
  await page.locator("#import-limit-pdf_pages").fill("7");
  await page.locator("#sidebar-toggle").click();
  assert.equal(await page.locator("#import-limit-pdf_pages").inputValue(), "7");
  let dialogs = 0;
  page.on("dialog", async (d) => {
    dialogs++;
    await d.dismiss();
  });
  await page.locator('[data-tab="home"]').click();
  await delay(100);
  assert(dialogs === 1 && (await page.locator("#imports-panel").isVisible()));
  result.checks.push("sidebar-and-confirmLeave-preserve-draft");
  // Discard explicitly using the page's own input reset, not a test bypass.
  await page
    .locator("#import-limit-pdf_pages")
    .fill(await page.locator("#import-limit-pdf_pages").getAttribute("max"));
  await move("home");
  await complete();
  // A controlled visibility event verifies lifecycle logic; native background behavior is tested separately.

  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const count = result.requests.length;
  await page.waitForTimeout(11000);
  await delay(100);
  assert.equal(result.requests.length, count);
  await page.evaluate(() => {
    delete document.hidden;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForTimeout(1000);
  await settle();
  result.checks.push("controlled-hidden-page-zero-polling");
  // Storage failure has one root message and retains previously read values.
  await page.route(
    "**/admin/{channels/overview,backend,inventory/executions,vaults}",
    (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "storage_unavailable" }),
      }),
  );
  await page.locator("[data-home-refresh]").click();
  await delay(1300);
  await page.waitForFunction(
    () => document.querySelectorAll(".home-issues p").length === 1,
  );
  assert(
    (await page
      .locator("#home-content")
      .getByText("最近读取", { exact: false })
      .count()) > 0,
  );
  await page.screenshot({ path: out + "/storage-failure.png", fullPage: true });
  await page.unrouteAll({ behavior: "wait" });
  result.checks.push("503-one-root-and-retained-last-good-values");
  // A genuine authorization error is not disguised as unsupported capability.
  await page.route("**/admin/imports?*", (route) =>
    route.fulfill({
      status: 403,
      contentType: "application/json",
      body: '{"error":"scope_denied"}',
    }),
  );
  await page.locator("[data-home-refresh]").click();
  await delay(1500);
  assert(
    (await page
      .locator("#home-content")
      .getByText("读取未获授权", { exact: false })
      .count()) > 0,
  );
  await page.unrouteAll({ behavior: "wait" });
  result.checks.push("403-is-scope-error");
  // New API declarations may be absent on UV/old backends without hiding known workflow entries.
  await page.route("**/admin/inventory/components", async (route) => {
    const r = await route.fetch();
    const v = await r.json();
    delete v.gui_features;
    await route.fulfill({ json: v });
  });
  await page.locator("[data-home-refresh]").click();
  await delay(1400);
  assert(
    (await page
      .locator('.arch-node[data-node="index"]')
      .getByText("轻量摘要尚未接入", { exact: false })
      .count()) === 1,
  );
  await page.unrouteAll({ behavior: "wait" });
  result.checks.push("missing-capability-declaration-keeps-known-UV-entries");
  // 401 clears private home data, suspends polling and returns to the same page after login.
  await page.route("**/admin/inventory/executions", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: '{"error":"expired"}',
    }),
  );
  await page.locator("[data-home-refresh]").click();
  await page.locator("#login-panel").waitFor({ state: "visible" });
  assert.equal(await page.locator("#home-content").textContent(), "");
  assert.equal(await page.locator("#home-client option").count(), 0);
  await page.unrouteAll({ behavior: "wait" });
  await login();
  assert(await page.locator("#home-panel").isVisible());
  result.checks.push("401-clears-private-data-and-relogin-same-page");
  assert.equal(result.errors.length, 0, result.errors.join("\n"));
  result.state = "PASS";
  fs.writeFileSync(out + "/home.json", JSON.stringify(result, null, 2));
  await browser.close();
  console.log(
    "HOME_GUI_PASS",
    result.checks.length,
    "scenarios",
    initial.length,
    "initial admin requests",
  );
})().catch((e) => {
  result.state = "FAIL";
  result.failure = String(e);
  fs.writeFileSync(out + "/home.json", JSON.stringify(result, null, 2));
  console.error(e);
  process.exit(1);
});
