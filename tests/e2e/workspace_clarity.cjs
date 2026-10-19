// Opt-in read-only GUI regression against a fresh synthetic fixture, never a deployed instance.
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
const pages = [
  "home",
  "worklist",
  "vaults",
  "imports",
  "inventory",
  "graph",
  "creation",
  "channels",
  "clients",
  "models",
  "execution",
  "backend",
  "backups",
  "settings",
];
const result = {
  synthetic: true,
  browser: "Browser plugin not available; existing Playwright",
  errors: [],
  writes: [],
  console: [],
  screenshots: [],
  checks: [],
};
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1180, height: 900 },
    });
    page.on("pageerror", (error) => result.errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") result.console.push(message.text());
    });
    page.on("request", (request) => {
      const p = new URL(request.url()).pathname;
      if (
        p.startsWith("/admin/") &&
        !["GET", "HEAD"].includes(request.method()) &&
        p !== "/admin/login"
      )
        result.writes.push(p);
    });
    async function settle() {
      await page.waitForFunction(
        () =>
          !document.querySelector(
            '#workspace > section:not([hidden])[aria-busy="true"]',
          ),
      );
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
    async function move(id) {
      await page.waitForFunction(() =>
        window.matchMedia("(max-width: 767px)").matches
          ? document.body.dataset.sidebar === "drawer"
          : document.body.dataset.sidebar !== "drawer",
      );
      const toggle = page.locator("#sidebar-toggle");
      if (
        (await page.evaluate(
          () => document.body.dataset.sidebar === "drawer",
        )) &&
        (await toggle.getAttribute("aria-expanded")) !== "true"
      )
        await toggle.click();
      await settle();
      await page.locator(`[data-tab="${id}"]`).click();
      await settle();
      if (id === "graph") {
        await page
          .locator("#graph-client")
          .selectOption({ label: "本机资料整理" });
        await page.locator("#graph-vault").selectOption({ label: "personal" });
        await page
          .getByRole("tab", { name: "整理笔记", exact: true })
          .waitFor();
      }
      if (
        (await page.evaluate(
          () => document.body.dataset.sidebar === "drawer",
        )) &&
        (await toggle.getAttribute("aria-expanded")) === "true"
      )
        await toggle.click();
      await settle();
      assert(await page.locator(`#${id}-panel`).isVisible());
    }
    await page.goto(base);
    assert.match(await page.title(), /Fiber bundle/);
    await page.locator("#password").fill("synthetic-gui-acceptance");
    await page.locator("#login-form button").click();
    await page.locator("#workspace").waitFor({ state: "visible" });
    await settle();
    await page
      .locator(".cost-card")
      .getByRole("button", { name: "明细", exact: true })
      .click();
    await settle();
    assert.equal(
      await page
        .locator("#workspace-execution-usage-tab")
        .getAttribute("aria-selected"),
      "true",
    );
    for (const width of [1180, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await move("home");
      for (const theme of ["oracle", "tesla", "cursor", "clay", "dark"]) {
        await page.evaluate(() => window.scrollTo(0, 0));
        await settle();
        const box = await page.locator("#theme-toggle").boundingBox();
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        await page.locator(`[data-theme-choice="${theme}"]`).click();
        await page.keyboard.press("Escape");
        for (const id of pages) {
          await move(id);
          assert(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth + 1,
            ),
            `${theme}/${id}/${width}: overflow`,
          );
          assert(
            (await page.locator(`#${id}-panel`).innerText()).trim().length > 0,
            `${id}: blank`,
          );
          result.checks.push(`${theme}/${id}/${width}`);
          if (
            ["graph", "vaults", "execution", "worklist", "settings"].includes(
              id,
            ) ||
            (theme === "dark" && width === 390)
          ) {
            const file = `${id}-${theme}-${width}.png`;
            await page.screenshot({
              path: path.join(out, file),
              fullPage: false,
            });
            result.screenshots.push(file);
          }
        }
      }
    }
    await page.setViewportSize({ width: 1180, height: 900 });
    await move("execution");
    await page.getByRole("tab", { name: "用量与费用", exact: true }).click();
    await page.getByRole("tab", { name: "执行任务", exact: true }).click();
    await page.keyboard.press("ArrowRight");
    assert.equal(
      await page
        .locator("#workspace-execution-usage-tab")
        .getAttribute("aria-selected"),
      "true",
    );
    await move("settings");
    const field = page.locator('[name="latinSize"]'),
      initial = await field.inputValue();
    await field.selectOption(initial === "20" ? "18" : "20");
    await page.getByRole("tab", { name: "登录与会话", exact: true }).click();
    await page.getByRole("tab", { name: "外观与阅读", exact: true }).click();
    assert.notEqual(await field.inputValue(), initial);
    await field.selectOption(initial);
    assert.deepEqual(result.errors, []);
    assert.deepEqual(result.writes, []);
    // The initial unauthenticated session read is expected; no other console errors pass.
    assert(
      result.console.every((text) => text.includes("401")),
      JSON.stringify(result.console),
    );
    result.state = "WORKSPACE_CLARITY_GUI_PASS";
    fs.writeFileSync(
      path.join(out, "checkpoint.json"),
      JSON.stringify(result, null, 2),
    );
    console.log(
      JSON.stringify({
        state: result.state,
        combinations: result.checks.length,
        screenshots: result.screenshots.length,
        errors: result.errors.length,
        writes: result.writes.length,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
