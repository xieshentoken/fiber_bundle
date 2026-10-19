// G1 state and text expansion checks. Injected errors/latency are explicitly recorded.
const {
  assert,
  chromium,
  base,
  entries,
  write,
  observe,
  settle,
  login,
  navigate,
  screenshot,
} = require("./gui_support.cjs");
const result = {
  stage: "G1 states",
  state: "RUNNING",
  page_errors: [],
  console: [],
  requests: [],
  screenshots: [],
  checks: [],
  contrast: [],
  text_scale: {
    factor: 2,
    method:
      "All computed font sizes and line heights doubled in the isolated DOM; text expansion pressure test, not native browser zoom",
  },
  native_browser_zoom:
    "NOT RUN; settings page unavailable in bundled headless browser",
  native_VoiceOver: "NOT RUN G3",
};
let browser;
async function contrast(page, id) {
  const rows = await page.evaluate(() => {
    const rgb = (v) => v.match(/[\d.]+/g)?.map(Number);
    const lum = (c) =>
      c
        .slice(0, 3)
        .map((v) => {
          v /= 255;
          return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        })
        .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
    const rows = [];
    for (const n of document.querySelectorAll("body *")) {
      if (
        !n.getClientRects().length ||
        n.matches(":disabled") ||
        !Array.from(n.childNodes).some(
          (c) => c.nodeType === 3 && c.textContent.trim(),
        )
      )
        continue;
      const style = getComputedStyle(n),
        fg = rgb(style.color);
      let p = n,
        bg;
      while (p) {
        const c = rgb(getComputedStyle(p).backgroundColor);
        if (c && c[3] !== 0) {
          bg = c;
          break;
        }
        p = p.parentElement;
      }
      if (!fg || !bg || fg[3] < 1 || bg[3] < 1 || Number(style.opacity) < 1)
        continue;
      const a = lum(fg),
        b = lum(bg),
        ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      const large =
        parseFloat(style.fontSize) >= 24 ||
        (parseFloat(style.fontSize) >= 18.66 &&
          Number(style.fontWeight) >= 700);
      rows.push({
        text: n.textContent.trim().slice(0, 80),
        ratio,
        minimum: large ? 3 : 4.5,
        tag: n.tagName,
      });
    }
    return rows;
  });
  result.contrast.push({
    entry: id,
    sampled_text: rows.length,
    failures: rows.filter((x) => x.ratio + 0.01 < x.minimum),
  });
}
async function doubledText(page) {
  return page.evaluate(() => {
    const nodes = Array.from(document.querySelectorAll("body,body *")).map(
      (n) => {
        const s = getComputedStyle(n);
        return {
          n,
          font: parseFloat(s.fontSize),
          line: parseFloat(s.lineHeight),
        };
      },
    );
    const sample = document.querySelector("#page-name"),
      before = parseFloat(getComputedStyle(sample).fontSize);
    for (const { n, font, line } of nodes) {
      n.style.fontSize = `${font * 2}px`;
      if (Number.isFinite(line)) n.style.lineHeight = `${line * 2}px`;
    }
    return {
      before,
      after: parseFloat(getComputedStyle(sample).fontSize),
      elements: nodes.length,
    };
  });
}
(async () => {
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 1180, height: 820 },
      locale: "zh-CN",
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    observe(page, result);
    await login(page);
    for (const [id] of entries) {
      await navigate(page, id);
      await contrast(page, id);
      let release, entered;
      const held = new Promise((r) => (release = r)),
        started = new Promise((r) => (entered = r));
      await page.route("**/admin/vaults", async (route) => {
        if (route.request().method() !== "GET") return route.continue();
        entered();
        await held;
        await route.continue();
      });
      await page.locator("#refresh").click();
      await started;
      await page.locator('#refresh[aria-busy="true"]').waitFor();
      await page.waitForFunction(
        () =>
          document.querySelector("#vault-list").getAttribute("aria-busy") ===
          "true",
      );
      await screenshot(page, result, `${id}-waiting`);
      release();
      await page.waitForFunction(
        () => !document.querySelector("#refresh").hasAttribute("aria-busy"),
      );
      await settle(page);
      await page.unroute("**/admin/vaults");
      for (const status of [500, 403]) {
        await page.route("**/admin/vaults", (route) =>
          route.fulfill({
            status,
            contentType: "application/json",
            body: JSON.stringify({ error: "synthetic_gui_read_failure" }),
          }),
        );
        await page.locator("#refresh").click();
        await page.waitForFunction(
          () => !document.querySelector("#refresh").hasAttribute("aria-busy"),
        );
        await page.locator("#notice.error").waitFor({ state: "visible" });
        await screenshot(
          page,
          result,
          `${id}-${status === 500 ? "read-failure" : "permission-denied"}`,
        );
        assert((await page.locator("#notice").innerText()).trim());
        await page.unroute("**/admin/vaults");
        await page.locator("#refresh").click();
        await page.waitForFunction(
          () => !document.querySelector("#refresh").hasAttribute("aria-busy"),
        );
        await settle(page);
      }
      result.checks.push({
        entry: id,
        wait: "PASS",
        error: "PASS",
        permission: "PASS",
        kind: "injected read latency/status; business behavior not mocked",
      });
      write("states", result);
    }
    for (const viewport of [
      { id: "desktop", width: 1180, height: 820 },
      { id: "reflow", width: 320, height: 844 },
    ]) {
      for (const [id] of entries) {
        const c = await browser.newContext({
            viewport: { width: viewport.width, height: viewport.height },
            locale: "zh-CN",
            reducedMotion: "reduce",
          }),
          p = await c.newPage();
        observe(p, result);
        await login(p);
        await navigate(p, id);
        const text = await doubledText(p);
        assert.equal(text.after / text.before, 2);
        await settle(p);
        const metrics = await screenshot(
          p,
          result,
          `${id}-text200-${viewport.id}`,
        );
        result.checks.push({
          entry: id,
          viewport: viewport.id,
          text_expansion: "200%",
          font_size_proof: text,
          state: metrics.scroll_width <= metrics.width ? "PASS" : "FAIL",
        });
        await c.close();
        write("states", result);
      }
    }
    assert.equal(result.page_errors.length, 0);
    assert(
      !result.checks.some((x) => x.state === "FAIL"),
      "200% text expansion caused horizontal overflow",
    );
    assert(
      !result.contrast.some((x) => x.failures.length),
      "sampled solid-color text contrast below threshold",
    );
    result.expected_http_errors = [401, 403, 500];
    result.business_writes = result.requests.filter(
      (x) =>
        !["GET", "HEAD", "OPTIONS"].includes(x.method) &&
        x.path !== "/admin/login",
    );
    assert.equal(result.business_writes.length, 0);
    result.state = "PASS";
    write("states", result);
    console.log(
      JSON.stringify({
        state: result.state,
        screenshots: result.screenshots.length,
        checks: result.checks.length,
        contrast_text: result.contrast.reduce((n, x) => n + x.sampled_text, 0),
      }),
    );
  } catch (error) {
    result.state = "FAIL";
    result.failure = error.message;
    write("states", result);
    console.error(error.message);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
})();
