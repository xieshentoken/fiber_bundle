// Read-only representative fixture previews. Confirmation and file applications are forbidden.
const { chromium } = require(process.env.FIBER_PLAYWRIGHT_MODULE);
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL || "http://127.0.0.1:18852",
  out = process.env.FIBER_GUI_EVIDENCE,
  meta = JSON.parse(fs.readFileSync(process.env.FIBER_REAL_FIXTURE));
fs.mkdirSync(out, { recursive: true });
const parsed = new URL(base);
assert(
  parsed.hostname === "127.0.0.1" &&
    +parsed.port >= 18000 &&
    +parsed.port <= 19000,
);
assert.equal(meta.real_models, false);
const result = {
  checks: [],
  errors: [],
  console: [],
  screenshots: [],
  synthetic: false,
  real_copies: 3,
  real_models: false,
  browser_plugin: "absent; existing Playwright1.63",
};
let browser, page;
(async () => {
  browser = await chromium.launch();
  page = await browser.newPage({ viewport: { width: 1180, height: 900 } });
  await page.route("**/admin/**", (route) => {
    const r = route.request(),
      p = new URL(r.url()).pathname;
    if (
      !["GET", "HEAD", "OPTIONS"].includes(r.method()) &&
      !["/admin/login", "/admin/organization/maintenance"].includes(p)
    ) {
      result.errors.push("unexpected mutation: " + p);
      return route.abort();
    }
    return route.continue();
  });
  page.on("pageerror", (e) => result.errors.push(e.message));
  page.on("console", (m) => {
    if (["warning", "error"].includes(m.type())) result.console.push(m.text());
  });
  const writes = [];
  page.on("request", (r) => {
    if (r.method() !== "GET" && r.url().includes("/admin/"))
      writes.push(new URL(r.url()).pathname);
  });
  await page.goto(base);
  assert.match(await page.title(), /Fiber bundle/);
  await page.locator("#password").fill("synthetic-maintenance-acceptance");
  await page.locator("#login-form button").click();
  await page.locator("#workspace").waitFor({ state: "visible" });
  await page.locator('[data-tab="graph"]').click();
  await page.locator("#graph-client").selectOption(meta.client_id);
  await page.locator("#graph-vault").selectOption(meta.vault_id);
  await page.locator("#graph-refresh").click();
  const panel = page.locator("#organization-maintenance");
  await panel.waitFor();
  assert.equal(
    await panel.locator('select[aria-label="整理模式"] option').count(),
    3,
  );
  const shot = async (name) => {
    await page.screenshot({
      path: path.join(out, name + ".png"),
      fullPage: false,
    });
    result.screenshots.push(name + ".png");
  };
  let releaseRefresh, observedRefresh;
  const refreshSeen = new Promise((resolve) => {
    observedRefresh = resolve;
  });
  const refreshReleased = new Promise((resolve) => {
    releaseRefresh = resolve;
  });
  let heldRefresh = false;
  await page.route("**/admin/organization?*", async (route) => {
    if (heldRefresh) return route.continue();
    heldRefresh = true;
    const response = await route.fetch();
    observedRefresh();
    await refreshReleased;
    await route.fulfill({ response });
  });
  await page.locator("#graph-refresh").click();
  await refreshSeen;
  async function mode(key) {
    const before = new Set(
      await panel
        .locator("[data-maintenance-job]")
        .evaluateAll((es) => es.map((e) => e.dataset.maintenanceJob)),
    );
    await panel
      .getByRole("combobox", { name: "整理模式", exact: true })
      .click();
    if (key === "additive") {
      const refreshed = page.waitForResponse(
        (r) =>
          r.request().method() === "GET" &&
          new URL(r.url()).pathname === "/admin/organization",
      );
      releaseRefresh();
      await refreshed;
      await page.evaluate(
        () =>
          new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(resolve)),
          ),
      );
      assert.equal(
        await panel
          .getByRole("combobox", { name: "整理模式", exact: true })
          .getAttribute("aria-expanded"),
        "true",
      );
      result.checks.push("inflight-refresh-preserves-open-picker");
    }
    await page
      .getByRole("option", {
        name: { quick: "快速整理", additive: "全量补充", deep: "深度重整" }[
          key
        ],
        exact: true,
      })
      .click();
    const responsePromise = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new URL(r.url()).pathname === "/admin/organization/maintenance",
    );
    await panel
      .getByRole("button", { name: "生成整理预览", exact: true })
      .click();
    const response = await responsePromise;
    assert.equal(response.status(), 200);
    const submitted = await response.json();
    const job = panel.locator(`[data-maintenance-job="${submitted.id}"]`);
    await job
      .getByRole("button", { name: "确认并执行本次整理", exact: true })
      .waitFor({ timeout: 60000 });
    assert.equal(
      await job
        .getByRole("button", { name: "确认并执行本次整理", exact: true })
        .isDisabled(),
      true,
    );
    await job.locator("details").first().locator("summary").click();
    assert((await job.locator("h4").textContent()).includes("预览待确认"));
    await job.evaluate((element) => element.scrollIntoView({ block: "start" }));
    await shot("preview-" + key);
    const text = await job.textContent();
    if (key === "quick")
      assert(text.includes("Resource/") && text.includes("Book/"));
    if (key === "deep") assert(text.includes("隔离对照"));
    result.checks.push(key + "-preview-diff-no-confirmation");
    return job;
  }
  await mode("additive");
  await mode("quick");
  await mode("deep");
  await shot("deep-preview");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(
    () =>
      document.body.dataset.sidebar === "drawer" &&
      document.body.dataset.drawerOpen === "false" &&
      document.getElementById("sidebar").getBoundingClientRect().right <= 1,
  );
  await panel.evaluate((element) => element.scrollIntoView({ block: "start" }));
  const box = await panel.boundingBox();
  assert(box.x >= 0 && box.x + box.width <= 391, JSON.stringify(box));
  await shot("mobile");
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  );
  assert.deepEqual(result.errors, []);
  assert(
    writes.every((p) =>
      ["/admin/login", "/admin/organization/maintenance"].includes(p),
    ),
    JSON.stringify(writes),
  );
  result.write_endpoints = writes;
  result.confirmation_actions = 0;
  assert.equal(
    await page.locator("vite-error-overlay,nextjs-portal").count(),
    0,
  );
  result.pass = true;
  await browser.close();
})()
  .catch(async (e) => {
    result.pass = false;
    result.failure = e.stack;
    if (page) await page.screenshot({ path: path.join(out, "failure.png") });
    if (browser) await browser.close();
    process.exitCode = 1;
  })
  .finally(() => {
    fs.writeFileSync(
      path.join(out, "results.json"),
      JSON.stringify(result, null, 2),
    );
    console.log(JSON.stringify(result));
  });
