const {
  assert,
  chromium,
  write,
  observe,
  login,
  navigate,
  settle,
  screenshot,
} = require("./gui_support.cjs");
const result = {
  stage: "G2 loading",
  state: "RUNNING",
  page_errors: [],
  console: [],
  requests: [],
  screenshots: [],
  checks: [],
};
let browser, release;
(async () => {
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
        viewport: { width: 1180, height: 820 },
      }),
      page = await context.newPage();
    observe(page, result);
    await login(page);
    let entered;
    const started = new Promise((r) => (entered = r)),
      held = new Promise((r) => (release = r));
    let requests = 0;
    await page.route("**/admin/agent-connections/catalog", async (route) => {
      requests++;
      entered();
      await held;
      await route.continue();
    });
    await page.locator('[data-tab="clients"]').click();
    await started;
    const create = page.getByRole("button", { name: "新建连接", exact: true }),
      refresh = page.getByRole("button", { name: "刷新连接", exact: true });
    await screenshot(page, result, "loading-catalog-controls");
    assert(
      await create.isDisabled(),
      "New connection is enabled before catalog loading completes",
    );
    assert(
      await refresh.isDisabled(),
      "Refresh can invalidate a pending catalog load",
    );
    release();
    await settle(page);
    await page.locator("#agent-config-form").waitFor({ state: "visible" });
    assert.equal(requests, 1);
    result.checks.push({
      name: "initial catalog gates create/refresh until completion",
      state: "PASS",
    });
    await page.unroute("**/admin/agent-connections/catalog");
    await page.route("**/admin/agent-connections/catalog", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: '{"error":"synthetic_catalog_failure"}',
      }),
    );
    await navigate(page, "vaults");
    await navigate(page, "clients");
    await page.locator("#notice.error").waitFor({ state: "visible" });
    assert(await create.isDisabled());
    assert(await refresh.isEnabled());
    await screenshot(page, result, "catalog-failure-retry");
    await page.unroute("**/admin/agent-connections/catalog");
    await refresh.click();
    await settle(page);
    await page.locator("#agent-config-form").waitFor({ state: "visible" });
    assert(await create.isEnabled());
    result.checks.push({
      name: "failed catalog keeps create disabled and permits explicit retry",
      state: "PASS",
    });
    let releaseLate, enterLate;
    const late = new Promise((r) => (releaseLate = r)),
      lateStarted = new Promise((r) => (enterLate = r));
    await page.route("**/admin/agent-connections/catalog", async (route) => {
      enterLate();
      await late;
      await route.continue();
    });
    await navigate(page, "vaults");
    await page.locator('[data-tab="clients"]').click();
    await lateStarted;
    await page.locator('[data-tab="backups"]').click();
    releaseLate();
    await settle(page);
    assert.equal(await page.locator("#page-name").innerText(), "备份与恢复");
    await page.unroute("**/admin/agent-connections/catalog");
    await navigate(page, "clients");
    await page.locator("#agent-config-form").waitFor({ state: "visible" });
    result.checks.push({
      name: "late catalog cannot repopulate a departed page; returning loads normally",
      state: "PASS",
    });
    assert.equal(result.page_errors.length, 0);
    let allowRefresh, enterRefresh;
    const refreshing = new Promise((r) => (enterRefresh = r)),
      heldRefresh = new Promise((r) => (allowRefresh = r));
    await page.route("**/admin/agent-connections/catalog", async (route) => {
      enterRefresh();
      await heldRefresh;
      await route.continue();
    });
    await refresh.click();
    await refreshing;
    assert(await page.locator("#agent-name").isDisabled());
    assert(
      await page
        .locator('#agent-config-form button[type="submit"]')
        .isDisabled(),
    );
    allowRefresh();
    await page.waitForFunction(
      () =>
        document
          .querySelector("#agent-connections")
          .getAttribute("aria-busy") === "false",
    );
    await settle(page);
    await page.unroute("**/admin/agent-connections/catalog");
    assert(await page.locator("#agent-name").isEnabled());
    result.checks.push({
      name: "manual refresh prevents edits to the form being replaced and restores controls",
      state: "PASS",
    });
    result.state = "PASS";
    write("loading", result);
    console.log(
      JSON.stringify({ state: result.state, checks: result.checks.length }),
    );
  } catch (error) {
    result.state = "FAIL";
    result.failure = error.message;
    write("loading", result);
    console.error(error.message);
    process.exitCode = 1;
  } finally {
    release?.();
    if (browser) await browser.close();
  }
})();
