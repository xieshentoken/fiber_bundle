// Layout regression on a synthetic instance; never use a production port.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL;
const out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out);
const url = new URL(base);
assert.equal(url.hostname, "127.0.0.1");
assert(+url.port >= 18000 && +url.port <= 19000);
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  const writes = [];
  const cases = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.method() !== "GET")
      writes.push(new URL(request.url()).pathname);
  });
  try {
    await page.goto(base);
    assert((await page.title()).includes("Fiber bundle"));
    await page.locator("#password").fill("synthetic-gui-acceptance");
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator("#workspace").waitFor({ state: "visible" });
    await page.locator(".home-system-details > summary").click();
    for (const language of ["en", "zh-CN"]) {
      await page
        .locator("[data-language-control]")
        .first()
        .selectOption(language, { force: true });
      for (const width of [1440, 1417, 1180, 1024, 390]) {
        for (const fontSize of [15, 18]) {
          await page.setViewportSize({ width, height: 900 });
          await page.evaluate((size) => {
            const root = document.documentElement;
            root.dataset.theme = "clay";
            root.style.fontSize = size + "px";
            root.style.setProperty("--font-ui", "Georgia, serif");
          }, fontSize);
          await page.evaluate(() => document.fonts.ready);
          await page.waitForTimeout(100);
          const boxes = await page.locator(".arch-entry").evaluateAll((rows) =>
            rows.map((row) => {
              const title = row.querySelector("strong");
              const state = row.querySelector(".arch-status");
              const t = title.getBoundingClientRect();
              const s = state.getBoundingClientRect();
              const r = row.getBoundingClientRect();
              return {
                title: title.textContent,
                titleWidth: t.width,
                rowWidth: r.width,
                titleBottom: t.bottom,
                stateTop: s.top,
                clipped: row.scrollWidth > row.clientWidth + 1,
              };
            }),
          );
          cases.push({ language, width, fontSize, boxes });
          if (language === "en" && width === 1417 && fontSize === 15)
            await page.locator(".arch-card").screenshot({
              path: out + "/english-architecture.png",
            });
          for (const box of boxes) {
            assert(box.titleWidth > box.rowWidth * 0.7, box.title);
            assert(box.stateTop >= box.titleBottom - 1, box.title);
            assert(!box.clipped, box.title);
          }
          const header = await page
            .locator(".arch-entries-head")
            .evaluate((node) => {
              const [title, state] = [...node.children].map((child) =>
                child.getBoundingClientRect(),
              );
              return { titleBottom: title.bottom, stateTop: state.top };
            });
          assert(header.stateTop >= header.titleBottom - 1);
          assert(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth + 1,
            ),
          );
        }
      }
    }
    // Node buttons still navigate after the layout change.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.locator('.arch-node[data-node="backend"]').click();
    await page.locator("#backend-panel").waitFor({ state: "visible" });
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, ["/admin/login"]);
    console.log("ARCHITECTURE_LAYOUT_PASS", cases.length);
  } catch (error) {
    await page.screenshot({ path: out + "/failure.png", fullPage: false });
    throw error;
  } finally {
    fs.writeFileSync(
      out + "/layout-results.json",
      JSON.stringify({ cases, errors, writes }, null, 2),
    );
    await browser.close();
  }
})();
