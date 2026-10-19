// Opt-in synthetic GUI regression; never connect this test to a real instance.
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL;
const out = process.env.FIBER_GUI_EVIDENCE;
const baseline = process.env.FIBER_CSS_BASELINE;
assert(base && out);
const url = new URL(base);
assert.equal(url.hostname, "127.0.0.1");
assert(+url.port >= 18000 && +url.port <= 19000);
const result = {
  synthetic: true,
  browser: "Browser plugin not available; existing Playwright",
  checks: [],
  errors: [],
  console: [],
  writes: [],
};
fs.mkdirSync(out, { recursive: true });
const stableProperties = [
  "boxShadow",
  "textShadow",
  "transform",
  "opacity",
  "filter",
  "animationName",
  "animationDuration",
  "animationTimingFunction",
  "animationDelay",
  "transitionProperty",
  "transitionDuration",
  "transitionTimingFunction",
  "transitionDelay",
  "borderRadius",
  "borderWidth",
  "borderStyle",
  "padding",
  "margin",
  "fontFamily",
  "fontSize",
  "fontWeight",
  "lineHeight",
  "letterSpacing",
  "outlineStyle",
  "outlineWidth",
  "outlineOffset",
  "outlineColor",
];
async function settle(page) {
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => {})),
    );
  });
}
async function open(page) {
  await page.goto(base);
  assert.match(await page.title(), /Fiber bundle/);
  assert.equal(new URL(page.url()).origin, url.origin);
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button").click();
  await page.locator("#workspace").waitFor({ state: "visible" });
  await settle(page);
  await page.locator('[data-tab="graph"]').click();
  await settle(page);
  await page
    .locator("#graph-client")
    .selectOption({ label: "G2 合成资料身份" });
  await page.locator("#graph-vault").selectOption({ label: "personal" });
  await page.getByRole("tab", { name: "整理笔记", exact: true }).waitFor();
  // Render the shared pressed-filter selectors without creating a business job.
  await page.evaluate(() => {
    const group = document.createElement("div");
    group.id = "highlight-filter-fixture";
    group.className = "task-filters";
    for (const [label, pressed] of [
      ["全部", true],
      ["待处理", false],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.setAttribute("aria-pressed", String(pressed));
      button.addEventListener("click", () => {
        for (const peer of group.children)
          peer.setAttribute("aria-pressed", String(peer === button));
      });
      group.append(button);
    }
    document.querySelector("#graph-panel").append(group);
  });
  assert.equal(
    await page.locator("vite-error-overlay,nextjs-portal").count(),
    0,
  );
  assert((await page.locator("#graph-panel").innerText()).trim().length > 40);
}
async function theme(page, id) {
  await page.locator("#theme-toggle").click();
  await page.locator(`[data-theme-choice="${id}"]`).click();
  await page.keyboard.press("Escape");
  await page.waitForFunction(
    (id) => document.documentElement.dataset.theme === id,
    id,
  );
  await settle(page);
}
async function snapshot(locator) {
  return locator.evaluate((node, properties) => {
    const s = getComputedStyle(node),
      r = node.getBoundingClientRect();
    const stable = Object.fromEntries(properties.map((p) => [p, s[p]]));
    stable.outlineColor =
      s.outlineStyle === "none" ? "not-painted" : s.outlineColor;
    stable.width = r.width;
    stable.height = r.height;
    const token = (name) => {
      const probe = document.createElement("span");
      probe.style.color = `var(${name})`;
      node.append(probe);
      const value = getComputedStyle(probe).color;
      probe.remove();
      return value;
    };
    return {
      stable,
      background: s.backgroundColor,
      color: s.color,
      border: s.borderTopColor,
      accent: token("--accent"),
      hover: token("--accent-hover"),
      ink: token("--on-accent"),
    };
  }, stableProperties);
}
async function inspect(page, width, id, solid) {
  await page.setViewportSize({ width, height: 900 });
  await theme(page, id);
  if (
    width < 768 &&
    (await page.locator("#sidebar-toggle").getAttribute("aria-expanded")) !==
      "true"
  )
    await page.locator("#sidebar-toggle").click();
  await settle(page);
  const selected = page.locator('.sidebar nav button[aria-current="page"]');
  const values = {};
  await page.mouse.move(width - 2, 2);
  await settle(page);
  values.sidebar = await snapshot(selected);
  values.sidebarIdle = await snapshot(page.locator('[data-tab="inventory"]'));
  await selected.hover();
  await settle(page);
  values.sidebarHover = await snapshot(selected);
  if (width < 768) await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "MOC 管理", exact: true }).click();
  await page.keyboard.press("ArrowLeft");
  await settle(page);
  const tab = page.getByRole("tab", { name: "整理笔记", exact: true });
  assert.equal(await tab.getAttribute("aria-selected"), "true");
  assert.equal(await tab.evaluate((n) => n === document.activeElement), true);
  values.tabFocus = await snapshot(tab);
  await tab.hover();
  await settle(page);
  values.tabHover = await snapshot(tab);
  values.tabIdle = await snapshot(
    page.getByRole("tab", { name: "MOC 管理", exact: true }),
  );
  const filter = page.locator("#highlight-filter-fixture button").last();
  await filter.click();
  await settle(page);
  assert.equal(await filter.getAttribute("aria-pressed"), "true");
  values.filter = await snapshot(filter);
  if (solid) {
    for (const name of ["sidebar", "tabFocus", "tabHover", "filter"]) {
      assert.equal(
        values[name].background,
        values[name].accent,
        `${id}/${width}/${name}: solid theme background`,
      );
      assert.equal(
        values[name].color,
        values[name].ink,
        `${id}/${width}/${name}: readable theme text`,
      );
    }
    assert.equal(values.sidebarHover.background, values.sidebarHover.hover);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  );
  if (id === "clay" && width === 1180)
    await page.screenshot({
      path: path.join(out, solid ? "after.png" : "before.png"),
      fullPage: false,
    });
  return values;
}
(async () => {
  const browser = await chromium.launch();
  try {
    const pages = [];
    for (const original of baseline ? [true, false] : [false]) {
      const context = await browser.newContext({
        viewport: { width: 1180, height: 900 },
        reducedMotion: "no-preference",
      });
      if (original)
        await context.route(
          /\/static\/(themes|workspace_clarity)\.css$/,
          (route) =>
            route.fulfill({
              path: path.join(
                baseline,
                new URL(route.request().url()).pathname.split("/").pop(),
              ),
              contentType: "text/css",
            }),
        );
      const page = await context.newPage();
      page.on("pageerror", (e) => result.errors.push(e.message));
      page.on("console", (m) => {
        if (m.type() === "error" && !m.text().includes("401"))
          result.console.push(m.text());
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
      await open(page);
      pages.push(page);
    }
    for (const width of [1180, 390])
      for (const id of ["clay", "oracle", "tesla", "cursor", "dark"]) {
        const before = baseline && (await inspect(pages[0], width, id, false));
        const after = await inspect(pages.at(-1), width, id, true);
        if (before)
          for (const name of Object.keys(after)) {
            assert.deepEqual(
              after[name].stable,
              before[name].stable,
              `${id}/${width}/${name}: preserve geometry, shadow, motion and focus`,
            );
            if (name.endsWith("Idle"))
              assert.deepEqual(
                after[name],
                before[name],
                `${id}/${width}/${name}: unselected appearance unchanged`,
              );
          }
        result.checks.push(`${id}/${width}`);
      }
    assert.deepEqual(result.errors, []);
    assert.deepEqual(result.console, []);
    assert.deepEqual(result.writes, []);
    result.state = "SOLID_SELECTION_HIGHLIGHT_PASS";
    fs.writeFileSync(
      path.join(out, "checkpoint.json"),
      JSON.stringify(result, null, 2) + "\n",
    );
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
