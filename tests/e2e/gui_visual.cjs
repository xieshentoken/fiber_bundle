// G1: real served UI references plus explicitly injected read failures; no business writes.
const {
  assert,
  chromium,
  base,
  entries,
  viewports,
  write,
  observe,
  settle,
  login,
  navigate,
  choose,
  screenshot,
} = require("./gui_support.cjs");
const result = {
  stage: "G1",
  state: "RUNNING",
  browser_plugin: "Browser plugin not available",
  screenshots: [],
  page_errors: [],
  console: [],
  requests: [],
  checks: [],
  timings: [],
};
let browser;
async function check(name, work) {
  await work();
  result.checks.push({ name, state: "PASS" });
  write("visual", result);
}
(async () => {
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 1180, height: 820 },
      locale: "zh-CN",
    });
    const page = await context.newPage();
    observe(page, result);
    const started = Date.now();
    await page.goto(base);
    await page.locator("#login-panel").waitFor({ state: "visible" });
    await settle(page);
    for (const v of viewports) {
      await page.setViewportSize({ width: v.width, height: v.height });
      await settle(page);
      const metrics = await screenshot(page, result, `login-${v.id}`);
      assert(metrics.scroll_width <= metrics.width);
    }
    await page.setViewportSize({ width: 1180, height: 820 });
    await login(page);
    result.timings.push({
      name: "first_authenticated_screen",
      milliseconds: Date.now() - started,
      includes_initial_login_references: true,
    });
    for (const viewport of viewports) {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      for (const [id] of entries) {
        const before = Date.now();
        await navigate(page, id);
        result.timings.push({
          name: id,
          viewport: viewport.id,
          milliseconds: Date.now() - before,
        });
        const metrics = await screenshot(page, result, `${id}-${viewport.id}`);
        assert.equal(metrics.title, "Fiber bundle · 本机管理");
        assert(
          metrics.scroll_width <= metrics.width,
          `${id} overflows at ${viewport.id}`,
        );
        if (
          ["clients", "models", "documents", "creation", "backups"].includes(id)
        )
          await screenshot(page, result, `${id}-${viewport.id}-full`, true);
      }
    }
    await page.setViewportSize({ width: 1180, height: 820 });
    await navigate(page, "clients");
    await check(
      "desktop detail shares the first screen with bounded connection list",
      async () => {
        const a = await page.locator(".agent-picker").boundingBox(),
          b = await page.locator(".agent-detail").boundingBox();
        assert(b.x > a.x + a.width);
        assert(b.y < 820);
        assert(
          await page
            .locator(".agent-list")
            .evaluate((n) => n.scrollHeight > n.clientHeight),
        );
      },
    );
    await choose(
      page,
      "长名称测试：中文知识库与 AB-123 项目 · Long connection name",
    );
    await screenshot(page, result, "agent-selected-desktop", true);
    await check(
      "section navigation moves focus below the sticky header",
      async () => {
        await page
          .getByRole("button", { name: "问答与结果", exact: true })
          .click();
        const boxes = await page.evaluate(() => ({
          header: document.querySelector("header").getBoundingClientRect()
            .bottom,
          focused: document.activeElement.getBoundingClientRect().top,
          in_test: document.activeElement.matches(".agent-test"),
        }));
        assert(boxes.in_test);
        assert(boxes.focused >= boxes.header);
      },
    );
    await screenshot(page, result, "agent-test-section-focus");
    await page.getByRole("button", { name: "配置与范围", exact: true }).click();
    await check(
      "keyboard model menu opens, closes and returns focus",
      async () => {
        const combo = page.getByRole("combobox", {
          name: "受管模型配置",
          exact: true,
        });
        await combo.focus();
        await page.keyboard.press("ArrowDown");
        assert.equal(await combo.getAttribute("aria-expanded"), "true");
        await screenshot(page, result, "model-menu-keyboard");
        await page.keyboard.press("Escape");
        assert.equal(await combo.getAttribute("aria-expanded"), "false");
        assert(await combo.evaluate((n) => n === document.activeElement));
      },
    );
    await check(
      "mobile drawer traps Tab and restores focus with Escape",
      async () => {
        await page.setViewportSize({ width: 390, height: 844 });
        await settle(page);
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.locator("#sidebar-toggle").click();
        await page.locator("#sidebar-close").focus();
        await page.keyboard.press("Shift+Tab");
        assert.equal(
          await page.evaluate(() => document.activeElement.dataset.tab),
          "backups",
        );
        await page.keyboard.press("Escape");
        assert.equal(
          await page.locator("#sidebar-toggle").getAttribute("aria-expanded"),
          "false",
        );
        assert.equal(
          await page.evaluate(() => document.activeElement.id),
          "sidebar-toggle",
        );
        await screenshot(page, result, "mobile-focus-restored");
        await page.setViewportSize({ width: 1180, height: 820 });
        await settle(page);
        assert((await page.locator("header").boundingBox()).height < 110);
      },
    );
    await check(
      "reduced motion disables transitions and animation",
      async () => {
        await page.emulateMedia({ reducedMotion: "reduce" });
        const styles = await page.locator("#sidebar").evaluate((n) => ({
          transition: getComputedStyle(n).transitionDuration,
          animation: getComputedStyle(n).animationName,
        }));
        assert.equal(styles.transition, "0s");
        assert.equal(styles.animation, "none");
        await page.emulateMedia({ reducedMotion: "no-preference" });
      },
    );
    const offline = await context.newPage();
    observe(offline, result);
    for (const viewport of viewports) {
      await offline.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await offline.goto(base + "/__fixture__/offline.html");
      assert.equal(await offline.title(), "Fiber bundle · 后台连接失败");
      assert.equal(
        await offline.locator('a[href="fiber://reconnect"]').count(),
        1,
      );
      const metrics = await screenshot(
        offline,
        result,
        `offline-${viewport.id}`,
      );
      assert(metrics.scroll_width <= metrics.width);
    }
    await offline.close();
    result.native_reconnect_and_VoiceOver = "NOT RUN; G3 native verification";
    result.page_errors.forEach((error) => assert.fail(error));
    result.business_mutations = result.requests.filter(
      (x) =>
        !["GET", "HEAD", "OPTIONS"].includes(x.method) &&
        x.path !== "/admin/login",
    );
    assert.equal(result.business_mutations.length, 0);
    result.state = "PASS";
    write("visual", result);
    console.log(
      JSON.stringify({
        state: result.state,
        screenshots: result.screenshots.length,
        checks: result.checks.length,
        page_errors: result.page_errors.length,
      }),
    );
  } catch (error) {
    result.state = "FAIL";
    result.failure = error.message;
    write("visual", result);
    console.error(error.message);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
})();
