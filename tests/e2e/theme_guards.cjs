/* global process, URL, document, window, getComputedStyle, requestAnimationFrame */
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
const u = new URL(base);
assert(u.hostname === "127.0.0.1" && +u.port >= 18000 && +u.port <= 19000);
assert(out);
fs.mkdirSync(out, { recursive: true });
const result = { synthetic: true, checks: [], errors: [], contrast: [] };
let browser, page;
(async () => {
  browser = await chromium.launch();
  page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", (e) => result.errors.push(e.message));
  page.on("dialog", (d) => d.dismiss());
  await page.goto(base);
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button").click();
  await page.locator("#workspace").waitFor({ state: "visible" });
  async function settle() {
    await page.locator("#page-loading").waitFor({ state: "hidden" });
    await page.waitForFunction(
      () => document.documentElement.dataset.fontReady === "true",
    );
  }
  async function move(id) {
    await page.locator(`[data-tab="${id}"]`).click();
    await settle();
  }
  async function theme(id) {
    // Click the visible sticky logo without locator auto-scroll of its DOM box.
    const box = await page.locator("#theme-toggle").boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await page.locator(`[data-theme-choice="${id}"]`).click();
    await page.keyboard.press("Escape");
    await page.locator("#theme-picker").waitFor({ state: "hidden" });
  }
  await settle();
  await move("imports");
  await page.locator('[data-engine="ordinary"]').click();
  await page.locator(".import-advanced summary").click();
  const input = page.locator("#import-limit-pdf_pages"),
    original = await input.inputValue();
  await input.fill("7");
  const before = await page.evaluate(() => ({
    y: window.scrollY,
    page: document.querySelector('[data-tab][aria-current="page"]').dataset.tab,
  }));
  let requests = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/admin/")) requests.push(r.url());
  });
  await theme("cursor");
  await settle();
  assert.equal(await input.inputValue(), "7");
  assert.deepEqual(
    await page.evaluate(() => ({
      y: window.scrollY,
      page: document.querySelector('[data-tab][aria-current="page"]').dataset
        .tab,
    })),
    before,
  );
  assert.deepEqual(requests, []);
  await page.locator('[data-tab="home"]').click();
  assert(await page.locator("#imports-panel").isVisible());
  await input.fill(original);
  result.checks.push("business-form-scroll-navigation-draft-no-http-preserved");
  await move("settings");
  await page.evaluate(() => {
    window.__fontLoad = document.fonts.load.bind(document.fonts);
    window.__fontPending = [];
    document.fonts.load = () =>
      new Promise((r) => window.__fontPending.push(r));
  });
  await page.locator('[name="latinSize"]').selectOption("16");
  await page.locator("#appearance-form button[type=submit]").click();
  await theme("tesla");
  await page.locator('[name="latinSize"]').selectOption("20");
  await theme("oracle");
  await page.evaluate(() => {
    const old = window.__fontPending.splice(0, window.__fontPending.length - 2);
    old.forEach((r) => r([]));
  });
  assert.equal(
    await page.locator("html").getAttribute("data-font-ready"),
    "false",
  );
  assert.equal(await page.locator('[name="latinSize"]').inputValue(), "20");
  await page.evaluate(() => {
    window.__fontPending.splice(0).forEach((r) => r([]));
    document.fonts.load = window.__fontLoad;
  });
  await settle();
  assert.equal(await page.locator('[name="latinSize"]').inputValue(), "20");
  assert.equal(await page.locator("html").getAttribute("data-theme"), "oracle");
  assert.equal(
    await page.evaluate(
      () => JSON.parse(localStorage.getItem("fiber.appearance.v1")).latinSize,
    ),
    16,
  );
  await page.locator('[data-tab="home"]').click();
  assert(await page.locator("#settings-panel").isVisible());
  await page.locator("#appearance-cancel").click();
  result.checks.push(
    "slow-font-settings-save-new-edit-fast-themes-generation-guard",
  );
  await page.locator('[name="cjkSize"]').selectOption("20");
  await page.locator('[name="latinSize"]').selectOption("12");
  await theme("cursor");
  await settle();
  assert.equal(
    await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue(
        "--script-line-scale",
      ),
    ),
    String(20 / 12),
  );
  await page.locator("#appearance-cancel").click();
  result.checks.push("independent-cjk-latin-size-kept-across-themes");
  // Exercise the actual renderer with local synthetic data, avoiding any graph jobs/API calls.
  await page.evaluate(async () => {
    const host = document.createElement("div");
    host.id = "theme-graph-fixture";
    host.style.width = "600px";
    host.style.height = "400px";
    document.body.append(host);
    const { createGraphRenderer } =
      await import("/static/graph_renderer.bundle.js");
    window.__themeGraph = createGraphRenderer(
      host,
      {
        nodes: [
          { id: "one", kind: "note", label: "资料一" },
          { id: "two", kind: "group", label: "资料二" },
        ],
        edges: [{ id: "edge", source: "one", target: "two", kind: "explicit" }],
      },
      {
        onSelect() {},
        onFailure(message) {
          throw new Error(message);
        },
        reducedMotion: true,
      },
    );
    window.__themeGraph.focus("one");
    window.__themeGraph.graph.setNodeAttribute("one", "fixed", true);
    window.__graphCamera = {
      ...window.__themeGraph.renderer.getCamera().getState(),
    };
    window.__graphX = window.__themeGraph.graph.getNodeAttribute("one", "x");
  });
  for (const id of ["oracle", "tesla", "cursor", "clay", "dark"]) {
    await theme(id);
    await settle();
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(r)));
    const graph = await page.evaluate(() => ({
      camera: window.__themeGraph.renderer.getCamera().getState(),
      oldCamera: window.__graphCamera,
      x: window.__themeGraph.graph.getNodeAttribute("one", "x"),
      oldX: window.__graphX,
      pinned: window.__themeGraph.graph.getNodeAttribute("one", "fixed"),
      running: window.__themeGraph.layout.isRunning(),
      edge: window.__themeGraph.graph.getEdgeAttribute("edge", "color"),
      expected: getComputedStyle(document.documentElement)
        .getPropertyValue("--graph-explicit")
        .trim(),
      selected: window.__themeGraph.renderer.getSetting("nodeReducer")(
        "one",
        window.__themeGraph.graph.getNodeAttributes("one"),
      ).highlighted,
    }));
    assert.deepEqual(graph.camera, graph.oldCamera);
    assert.equal(graph.x, graph.oldX);
    assert.equal(graph.pinned, true);
    assert.equal(graph.running, false);
    assert.equal(graph.edge, graph.expected);
    assert.equal(graph.selected, true);
  }
  await page.evaluate(() => {
    window.__themeGraph.kill();
    document.querySelector("#theme-graph-fixture").remove();
  });
  result.checks.push(
    "sigma-theme-redraw-preserves-camera-selection-pins-and-layout",
  );
  await move("execution");
  await page.evaluate(async () => {
    const { renderUsageChart } = await import("/static/usage_chart.js");
    const root = document.createElement("div"),
      detail = document.createElement("p");
    root.id = "theme-chart-fixture";
    root.className = "usage-chart-scroll";
    document.querySelector("#execution-panel").prepend(root, detail);
    renderUsageChart(
      root,
      [
        {
          day: "2026-10-12",
          calls: 4,
          estimated_nusd: 10000000,
          reserved_nusd: 0,
        },
      ],
      detail,
    );
  });
  for (const id of ["oracle", "tesla", "cursor", "clay", "dark"]) {
    await theme(id);
    await settle();
    const colors = await page.evaluate(() => {
      const css = getComputedStyle(document.documentElement);
      return {
        bar: getComputedStyle(
          document.querySelector("#theme-chart-fixture .chart-bar"),
        ).fill,
        line: getComputedStyle(
          document.querySelector("#theme-chart-fixture .chart-cost"),
        ).stroke,
        calls: css.getPropertyValue("--chart-calls").trim(),
        cost: css.getPropertyValue("--chart-cost").trim(),
      };
    });
    assert.notEqual(colors.bar, colors.line);
    await page.screenshot({
      path: path.join(out, `fees-${id}.png`),
      animations: "disabled",
    });
  }
  result.checks.push("svg-bar-line-recolor-and-legend-semantics");
  // Measure computed role colors, including alpha backgrounds over the actual panel.
  for (const id of ["oracle", "tesla", "cursor", "clay", "dark"]) {
    await theme(id);
    await settle();
    const colors = await page.evaluate(() => {
      const css = getComputedStyle(document.documentElement);
      const roles = [
        "--text",
        "--muted",
        "--surface-base",
        "--surface-panel",
        "--surface-raised",
        "--accent",
        "--on-accent",
        "--brand-text",
        "--nav-selected",
        "--nav-selected-ink",
        "--error-text",
        "--error-soft",
        "--status-connected",
        "--status-connected-soft",
        "--status-waiting",
        "--status-waiting-soft",
        "--status-info",
        "--status-info-soft",
      ];
      return Object.fromEntries(
        roles.map((n) => [n, css.getPropertyValue(n).trim()]),
      );
    });
    function rgb(value) {
      if (value.startsWith("#"))
        return value
          .slice(1)
          .match(/../g)
          .map((n) => parseInt(n, 16) / 255)
          .concat(1);
      const values = value.match(/[\d.]+/g).map(Number);
      return values
        .slice(0, 3)
        .map((n) => n / 255)
        .concat(values[3] ?? 1);
    }
    function over(value, background) {
      const c = rgb(value),
        b = rgb(background);
      return c.slice(0, 3).map((n, i) => n * c[3] + b[i] * (1 - c[3]));
    }
    function lum(channels) {
      const c = channels.map((n) =>
        n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4,
      );
      return c[0] * 0.2126 + c[1] * 0.7152 + c[2] * 0.0722;
    }
    for (const [fg, bg] of [
      ["--text", "--surface-base"],
      ["--muted", "--surface-panel"],
      ["--muted", "--surface-base"],
      ["--on-accent", "--accent"],
      ["--brand-text", "--surface-base"],
      ["--nav-selected-ink", "--nav-selected"],
      ["--error-text", "--error-soft"],
      ["--status-connected", "--status-connected-soft"],
      ["--status-waiting", "--status-waiting-soft"],
      ["--status-info", "--status-info-soft"],
    ]) {
      const a = lum(over(colors[fg], colors[bg])),
        b = lum(over(colors[bg], colors["--surface-panel"])),
        ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      result.contrast.push({ theme: id, fg, bg, ratio });
      assert(ratio >= 4.5, `${id}: ${fg}/${bg} ${ratio}`);
    }
  }
  result.checks.push("fifty-computed-text-contrast-pairs-pass");
  assert.deepEqual(result.errors, []);
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
      path.join(out, "guards-results.json"),
      JSON.stringify(result, null, 2),
    );
    console.log(
      JSON.stringify({
        pass: result.pass,
        checks: result.checks,
        contrast_pairs: result.contrast.length,
        errors: result.errors,
        failure: result.failure,
      }),
    );
  });
