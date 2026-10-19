// Inspect changed production pages without mutating the synthetic business state.
const { chromium } = require("playwright"),
  assert = require("node:assert/strict"),
  fs = require("node:fs");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE,
  entries = (process.env.FIBER_GUI_PAGES || "vaults,imports,inventory").split(
    ",",
  );
assert(base && out);
const u = new URL(base);
assert(u.hostname === "127.0.0.1" && +u.port >= 18000 && +u.port <= 19000);
fs.mkdirSync(out, { recursive: true });
(async () => {
  const lint = process.env.FIBER_GUI_LINT === "1";
  const inspect = lint ? (await import("lintapple")).checkPage : null;
  const contracts = lint
    ? (await import("./lintapple_guards.mjs")).checkContracts
    : null;
  const policy = lint
    ? JSON.parse(
        fs.readFileSync(
          require("node:path").resolve(__dirname, "../../ui-policy.json"),
        ),
      )
    : null;
  const findings = [];
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
      writes.push(new URL(r.url()).pathname);
  });
  async function settle() {
    await page.locator("#page-loading").waitFor({ state: "hidden" });
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
  }
  await page.goto(base);
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button").click();
  await page.locator("#home-cost-title").waitFor();
  await settle();
  for (const theme of ["cursor", "oracle", "tesla"]) {
    await page.locator("#theme-toggle").click();
    await page.locator(`[data-theme-choice="${theme}"]`).click();
    for (const id of entries) {
      await page.setViewportSize({ width: 1180, height: 820 });
      if (
        (await page
          .locator('[data-tab="' + id + '"]')
          .getAttribute("hidden")) !== null
      ) {
        await page.locator("[data-tab=worklist]").click();
        await settle();
        await page
          .locator('.inbox-pages [data-page-target="' + id + '"]')
          .click();
      } else await page.locator('[data-tab="' + id + '"]').click();
      await settle();
      assert(await page.locator("#" + id + "-panel").isVisible());
      if (id === "imports") {
        await page.locator("[data-engine=ordinary]").click();
        assert(await page.locator("#import-form").isVisible());
        assert.equal(await page.locator(".engine-card").count(), 2);
      }
      for (const width of [1440, 1180, 390]) {
        await page.setViewportSize({ width, height: 844 });
        await settle();
        if (lint) {
          await contracts(page);
          const checked = await inspect(page, policy);
          findings.push({ id, theme, width, findings: checked.findings });
          fs.writeFileSync(
            out + "/lint.json",
            JSON.stringify(findings, null, 2),
          );
        }
        assert(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          `overflow ${id}/${theme}/${width}`,
        );
        await page.screenshot({
          path: out + `/${id}-${theme}-${width}.png`,
          fullPage: true,
        });
      }
    }
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  assert(
    !findings.some((r) => r.findings.length),
    "UI lint findings: see lint.json",
  );
  fs.writeFileSync(
    out + "/result.json",
    JSON.stringify(
      { pages: entries, themes: 3, widths: 3, errors, writes, synthetic: true },
      null,
      2,
    ),
  );
  await browser.close();
  console.log(
    "PASS: " +
      entries.join(",") +
      " three themes/three widths, zero business writes",
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
