// Real review navigation and modal cancellation against the isolated fixture.
const { chromium } = require("playwright"),
  assert = require("node:assert/strict"),
  fs = require("node:fs");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out);
const u = new URL(base);
assert(u.hostname === "127.0.0.1" && +u.port >= 18000 && +u.port <= 19000);
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch(),
    page = await browser.newPage({ viewport: { width: 1180, height: 820 } }),
    errors = [],
    writes = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (
      r.url().includes("/admin/") &&
      r.method() !== "GET" &&
      !r.url().endsWith("/login")
    )
      writes.push(r.url());
  });
  await page.goto(base);
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button").click();
  await page.locator("#home-cost-title").waitFor();
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page.locator("[data-tab=worklist]").click();
  await page.locator(".inbox-filters").waitFor();
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page
    .locator(".inbox-filters button")
    .filter({ hasText: "文档" })
    .click();
  assert.equal(
    await page.locator(".inbox-rows [data-task-kind=documents]").count(),
    1,
  );
  await page
    .locator("#worklist-panel .inbox-pages [data-page-target=documents]")
    .click();
  await page.locator("#documents-panel .ui-task").waitFor();
  assert(await page.locator("#page-breadcrumb").isVisible());
  await page
    .locator("#documents-panel")
    .getByRole("button", { name: "取消文档任务", exact: true })
    .click();
  await page.locator(".confirm-dialog").waitFor();
  assert.equal(
    await page.evaluate(() => document.activeElement.textContent),
    "保留任务",
  );
  await page
    .locator(".confirm-dialog")
    .getByRole("button", { name: "保留任务" })
    .click();
  await page.locator(".confirm-dialog").waitFor({ state: "detached" });
  assert.deepEqual(writes, []);
  await page.screenshot({ path: out + "/documents.png", fullPage: true });
  await page.locator("#page-breadcrumb button").click();
  await page.locator(".inbox-filters").waitFor();
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  for (const width of [1180, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise(requestAnimationFrame);
      await Promise.all(
        document
          .getAnimations()
          .filter((a) => a.effect.getComputedTiming().iterations !== Infinity)
          .map((a) => a.finished.catch(() => {})),
      );
    });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `inbox overflow ${width}`,
    );
    await page.screenshot({
      path: out + `/inbox-${width}.png`,
      fullPage: true,
    });
  }
  assert.deepEqual(errors, []);
  fs.writeFileSync(
    out + "/result.json",
    JSON.stringify(
      {
        checks: [
          "source-filter",
          "document-scope-navigation",
          "breadcrumb",
          "destructive-cancel-safe-focus-zero-request",
          "three-width-inbox",
        ],
        errors,
        writes,
      },
      null,
      2,
    ),
  );
  await browser.close();
  console.log(
    "PASS: inbox, review navigation, safe cancellation, three widths",
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
