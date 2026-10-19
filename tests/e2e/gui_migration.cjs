// Opt-in production UI with a synthetic loopback backend and fresh browser profile.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out);
const url = new URL(base);
assert(
  url.hostname === "127.0.0.1" && +url.port >= 18000 && +url.port <= 19000,
);
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();
  page.on("console", (m) => {
    if (m.text().includes("G2")) console.log(m.text());
  });
  const gets = [];
  page.on("response", (r) => {
    if (r.url().includes("/admin/backend")) gets.push(r.status());
  });
  const errors = [],
    writes = [];
  page.on("dialog", async (d) => {
    console.log("Unexpected dialog:", d.message());
    await d.dismiss();
  });
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (
      new URL(r.url()).pathname.startsWith("/admin/") &&
      r.method() !== "GET" &&
      !r.url().endsWith("/login")
    )
      writes.push({ path: new URL(r.url()).pathname, method: r.method() });
  });
  await page.goto(base);
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button").click();
  await page.locator("#home-cost-title").waitFor();
  await page.getByText("UTC · 全部云凭据", { exact: false }).waitFor();
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
  assert.equal(await page.locator(".arch-node").count(), 8);
  assert.equal(await page.locator("#home-scope").count(), 1);
  for (const theme of ["cursor", "oracle", "tesla"]) {
    await page.locator("#theme-toggle").click();
    await page.locator(`[data-theme-choice="${theme}"]`).click();
    await page.waitForFunction(
      (t) => document.documentElement.dataset.theme === t,
      theme,
    );
    for (const width of [1440, 1180, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
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
      if (
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        )
      )
        console.log(
          await page.evaluate(() =>
            [
              "main",
              "#home-panel",
              "#home-content",
              ".home-columns",
              ".home-column",
            ].map((sel) => {
              const e = document.querySelector(sel),
                s = getComputedStyle(e),
                b = e.getBoundingClientRect();
              return {
                sel,
                width: b.width,
                left: b.left,
                container: s.containerType,
                grid: s.gridTemplateColumns,
                margin: s.marginLeft,
              };
            }),
          ),
        );
      if (
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        )
      )
        console.log(
          await page.evaluate(() =>
            [...document.querySelectorAll("main *")]
              .filter((e) => e.getBoundingClientRect().right > innerWidth + 1)
              .slice(0, 15)
              .map((e) => ({
                tag: e.tagName,
                cls: e.className,
                id: e.id,
                width: e.getBoundingClientRect().width,
                right: e.getBoundingClientRect().right,
              })),
          ),
        );
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `home overflow ${theme}/${width}`,
      );
      await page.screenshot({
        path: `${out}/home-${theme}-${width}.png`,
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await page
    .locator("#home-cost-title")
    .locator("..")
    .getByRole("button", { name: "表格", exact: true })
    .click();
  assert.equal(await page.locator(".cost-table tbody tr").count(), 30);
  await page.keyboard.press("Control+k");
  await page.locator("#command-palette input").fill("资料导入");
  assert(await page.locator("#command-palette").isVisible());
  await page.keyboard.press("Escape");
  await page.locator("#command-palette").waitFor({ state: "hidden" });
  await page.locator('#home-panel [data-import-engine="fake-expert"]').click();
  await page.waitForFunction(
    () => document.getElementById("import-engine")?.value === "fake-expert",
  );
  await page.locator('[data-tab="home"]').click();
  await page.locator("#home-cost-title").waitFor();
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page.route("**/admin/backend", (route) =>
    route.fulfill({ status: 503, json: { error: "storage_unavailable" } }),
  );
  await page.locator("#page-refresh").click();
  try {
    await page.locator(".home-alert").waitFor({ timeout: 30000 });
  } catch (error) {
    console.log("Errors/backend", errors, gets);
    console.log(
      "Refresh diagnostic",
      await page.evaluate(() => ({
        active: document.querySelector("[data-tab][aria-current]")?.dataset.tab,
        notice: document.getElementById("notice").textContent,
        refresh: document.getElementById("page-refresh").outerHTML,
        issues: document.querySelector(".home-issues")?.textContent,
      })),
    );
    throw error;
  }
  assert(await page.locator(".home-alert").isVisible());
  await page.screenshot({ path: `${out}/home-error.png`, fullPage: true });
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  fs.writeFileSync(
    `${out}/result.json`,
    JSON.stringify(
      {
        checks: [
          "three-theme-four-widths",
          "observed-map",
          "cost-table",
          "shortcut-and-Escape",
          "import-routing",
          "storage-error",
        ],
        errors,
        writes,
        synthetic: true,
      },
      null,
      2,
    ),
  );
  await browser.close();
  console.log(
    "PASS: GUI migration home, 12 theme/viewport scenarios, errors and zero business writes",
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
