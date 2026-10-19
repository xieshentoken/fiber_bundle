/* global process, URL, document, innerWidth, console */
// Opt-in synthetic GUI regression: fixed search, independently scrollable navigation.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const base = new URL(process.env.FIBER_GUI_ACCEPTANCE_URL);
assert.equal(base.hostname, "127.0.0.1");
assert(Number(base.port) >= 18000 && Number(base.port) <= 19000);
const output = process.env.FIBER_GUI_EVIDENCE;
assert(output && !fs.existsSync(output), "Use a fresh evidence directory");
fs.mkdirSync(output, { recursive: true, mode: 0o700 });
const report = {
  state: "RUNNING",
  browser: "Browser plugin not available; existing Playwright",
  checks: [],
  errors: [],
  expected_auth_probes: [],
};
const rect = async (page) =>
  page.evaluate(() => {
    const box = (s) => {
      const b = document.querySelector(s).getBoundingClientRect();
      return { top: b.top, bottom: b.bottom, left: b.left, right: b.right };
    };
    return {
      search: box("#command-open"),
      nav: box("#sidebar nav"),
      logo: box("header"),
      footer: box("#sidebar-footer"),
      asideScroll: document.querySelector("#sidebar").scrollTop,
      navScroll: document.querySelector("#sidebar nav").scrollTop,
    };
  });
(async () => {
  const browser = await chromium.launch();
  try {
    for (const [width, height, state, language, theme] of [
      [1180, 540, "expanded", "zh-CN", "clay"],
      [900, 540, "rail", "en", "dark"],
      [390, 740, "drawer", "zh-CN", "clay"],
    ]) {
      const page = await browser.newPage({ viewport: { width, height } });
      let authenticated = false;
      page.on("response", (r) => {
        if (r.status() === 401)
          report.expected_auth_probes.push(new URL(r.url()).pathname);
      });
      page.setDefaultTimeout(10000);
      page.on("pageerror", (e) => report.errors.push(e.message));
      page.on("console", (m) => {
        if (
          m.type() === "error" &&
          !(!authenticated && m.text().includes("401 (Unauthorized)"))
        )
          report.errors.push(m.text());
      });
      await page.goto(base.href);
      assert.match(await page.title(), /Fiber bundle/);
      await page.locator("#login-form").waitFor();
      await page.locator("#password").fill("synthetic-gui-acceptance");
      await page.locator("#login-form button").click();
      await page.locator("#workspace").waitFor({ state: "visible" });
      authenticated = true;
      await page
        .locator("[data-language-control]")
        .first()
        .selectOption(language, { force: true });
      if (theme === "dark") {
        await page.locator("#theme-toggle").click();
        await page.locator("[data-theme-choice=dark]").click();
        await page.keyboard.press("Escape");
      }
      if (state === "drawer") await page.locator("#sidebar-toggle").click();
      await page.waitForFunction(
        (s) => document.body.dataset.sidebar === s,
        state,
      );
      await page.evaluate(() => document.fonts.ready);
      const before = await rect(page);
      if (state !== "drawer")
        assert(before.search.top >= before.logo.bottom - 1);
      assert(
        before.nav.top >= before.search.bottom - 1,
        "Navigation must have its own space below search",
      );
      assert(before.nav.bottom <= before.footer.top + 1);
      await page.locator("#sidebar nav").evaluate((n) => {
        n.scrollTop = n.scrollHeight;
      });
      const scrolled = await rect(page);
      assert(scrolled.navScroll > 0);
      assert.equal(scrolled.asideScroll, 0);
      assert(Math.abs(scrolled.search.top - before.search.top) < 1);
      await page.locator("#sidebar").evaluate((n) => {
        n.scrollTop = 100;
      });
      assert.equal((await rect(page)).asideScroll, 0);
      const buttons = page.locator(
        "#sidebar nav button[data-tab]:not([hidden])",
      );
      for (let i = 0; i < (await buttons.count()); i++) {
        const button = buttons.nth(i);
        await button.scrollIntoViewIfNeeded();
        assert(
          await button.evaluate((n) => {
            const b = n.getBoundingClientRect();
            return (
              document
                .elementFromPoint(b.x + b.width / 2, b.y + b.height / 2)
                ?.closest("button") === n
            );
          }),
          "Every navigation button must be unobscured after scrolling into view",
        );
      }
      await page.locator("#sidebar nav").evaluate((n) => {
        n.scrollTop = 0;
      });
      await page.screenshot({ path: path.join(output, state + ".png") });
      await page.locator("#sidebar [data-tab=settings]").click();
      await page.locator("#settings-panel").waitFor({ state: "visible" });
      if (state === "drawer") await page.locator("#sidebar-toggle").click();
      await page.locator("#command-open").click();
      await page.locator("#command-palette").waitFor({ state: "visible" });
      await page.keyboard.press("Escape");
      if (state === "drawer") await page.keyboard.press("Escape");
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
      );
      report.checks.push({
        width,
        height,
        state,
        language,
        theme,
        fixed_search: true,
        independent_nav_scroll: true,
        all_buttons_unobscured: true,
        navigation_and_search_work: true,
      });
      await page.close();
    }
    assert.deepEqual(report.errors, []);
    assert(
      report.expected_auth_probes.every((path) => path === "/admin/session"),
    );
    report.state = "PASS";
  } finally {
    await browser.close();
    fs.writeFileSync(
      path.join(output, "checkpoint.json"),
      JSON.stringify(report, null, 2),
    );
  }
  console.log(JSON.stringify(report));
})().catch((e) => {
  console.error(e.stack);
  process.exitCode = 1;
});
