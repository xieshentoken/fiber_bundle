/* global process, URL, document, innerWidth, console, getComputedStyle */
// Rendered evidence states use response fixtures; no provider, channel or publication is started.
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const assert = require("node:assert/strict"),
  fs = require("node:fs");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out);
const u = new URL(base);
assert(u.hostname === "127.0.0.1" && +u.port >= 18000 && +u.port <= 19000);
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1180, height: 790 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const channels = [
    {
      id: "paused",
      name: "主动暂停入口",
      state: "paused",
      binding_state: "bound",
      runtime: { heartbeat_fresh: false },
      transport: "disconnected",
      observed_at: 1700000000,
    },
    {
      id: "stale",
      name: "心跳过期入口",
      state: "running",
      binding_state: "bound",
      runtime: { heartbeat_fresh: false },
      transport: "unknown",
      observed_at: 1700000000,
    },
    {
      id: "ready",
      name: "有观察的入口",
      state: "running",
      binding_state: "bound",
      runtime: { heartbeat_fresh: true },
      transport: "connected",
      observed_at: 1700000000,
    },
  ];
  await page.route("**/admin/query/policy", (r) =>
    r.fulfill({ json: { configured: false } }),
  );
  await page.route("**/admin/channels/overview", (r) =>
    r.fulfill({ json: { channels } }),
  );
  await page.route("**/admin/inventory/executions", (r) =>
    r.fulfill({
      json: {
        runs: [
          {
            id: "historical-failure",
            state: "failed",
            created_at: 1700000000,
            updated_at: 1700000010,
            binding_kind: "source",
            calls: [{ model: "historical-model", state: "settled" }],
          },
        ],
        daily_costs: [],
      },
    }),
  );
  await page.goto(base);
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button").click();
  await page
    .locator("#home-content")
    .getByText("UTC · 全部云凭据", { exact: false })
    .waitFor();
  assert.equal(await page.locator(".home-issues p").count(), 1);
  assert(
    await page
      .locator(".home-issues")
      .getByText("心跳过期入口", { exact: false })
      .isVisible(),
  );
  assert.equal(
    await page
      .locator(".home-issues")
      .getByText("主动暂停入口", { exact: false })
      .count(),
    0,
  );
  assert(
    await page
      .locator("#home-content")
      .getByText("传输已连接", { exact: true })
      .isVisible(),
  );
  assert(
    await page
      .locator("#home-content")
      .getByText("历史执行，非当前连通性检查", { exact: false })
      .isVisible(),
  );
  assert(
    await page
      .locator("#home-content")
      .getByText("模型：尚未配置", { exact: true })
      .isVisible(),
  );
  await page.screenshot({ path: out + "/observed-states.png", fullPage: true });
  // Delayed old scope may finish at the server but cannot repopulate the new Vault.
  let release, startedResolve;
  const started = new Promise((r) => (startedResolve = r));
  let first = true;
  await page.route("**/admin/imports?*", async (r) => {
    if (first) {
      first = false;
      startedResolve();
      await new Promise((x) => (release = x));
      try {
        await r.fulfill({
          json: [
            {
              id: "stale-scope",
              filename: "OLD_SCOPE_MUST_NOT_APPEAR",
              state: "needs_review",
            },
          ],
        });
      } catch {
        /* The old scoped request may already have been aborted. */
      }
    } else
      await r.fulfill({
        json: [
          {
            id: "new-scope",
            filename: "NEW_SCOPE_VISIBLE",
            state: "needs_review",
          },
        ],
      });
  });
  await page.locator("[data-home-refresh]").click();
  await started;
  const options = await page
    .locator("#home-vault option")
    .evaluateAll((nodes) => nodes.map((n) => n.value));
  assert.equal(options.length, 2);
  const current = await page.locator("#home-vault").inputValue();
  await page
    .locator("#home-vault")
    .selectOption(options.find((x) => x !== current));
  release();
  await page
    .locator(".imports-card")
    .getByText("NEW_SCOPE_VISIBLE", { exact: false })
    .waitFor();
  assert.equal(
    await page
      .locator("#home-content")
      .getByText("OLD_SCOPE_MUST_NOT_APPEAR", { exact: false })
      .count(),
    0,
  );
  // Use the source-qualified link; the target list can legitimately change before navigation.
  await page.screenshot({
    path: out + "/scope-replacement.png",
    fullPage: true,
  });
  await page.unrouteAll({ behavior: "wait" });
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.locator("[data-home-refresh]").click();
  await page.waitForTimeout(800);
  assert.equal(
    await page
      .locator("#home-content")
      .evaluate((n) => getComputedStyle(n).animationName),
    "none",
  );
  await page.screenshot({
    path: out + "/dark-reduced-motion.png",
    fullPage: true,
  });
  // 200% actual browser zoom through Chromium CSS zoom with overflow measurement.
  await page.evaluate(() => {
    document.documentElement.style.zoom = "2";
  });
  await page.waitForTimeout(350);
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "200% overflow",
  );
  await page.screenshot({ path: out + "/zoom-200.png", fullPage: true });
  assert.equal(errors.length, 0, errors.join("\n"));
  fs.writeFileSync(
    out + "/states.json",
    JSON.stringify(
      {
        state: "PASS",
        checks: [
          "paused-vs-heartbeat-vs-transport",
          "historical-model-not-health",
          "unconfigured-route",
          "old-scope-response-discard",
          "reduced-motion",
          "CSS-zoom-200-percent",
        ],
        errors,
        synthetic: true,
        native_browser_zoom: false,
      },
      null,
      2,
    ),
  );
  await browser.close();
  console.log("HOME_STATES_PASS");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
