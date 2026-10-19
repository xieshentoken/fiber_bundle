// Run with FIBER_PLAYWRIGHT_MODULE and optional FIBER_SELECT_SOURCE / FIBER_SELECT_ELECTRON.
// Exercises the real shared select module in an isolated, synthetic page.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { chromium, _electron } = require(process.env.FIBER_PLAYWRIGHT_MODULE);
const root =
  process.env.FIBER_SELECT_SOURCE || path.resolve(__dirname, "../..");
const output = process.env.FIBER_GUI_EVIDENCE;
const html = `<!doctype html><meta charset="utf-8"><title>Font dropdown regression</title>
<link rel="stylesheet" href="/static/controls.css"><style>
*{box-sizing:border-box}body{margin:0;font:14px sans-serif;--surface-raised:white;--text:#222;--line:#ccc;--surface-hover:#eee;--on-accent:white;--radius-sm:12px}
main{height:100vh;overflow:auto;padding:24px}label{display:block;margin-top:120px;width:min(360px,90%)}.select-field{display:block}.select-trigger{width:100%}footer{height:900px}
</style><main><h1>字体滚动验收</h1><div id="status">Ready</div><label>英文字体<select id="font"></select></label><footer></footer></main>
<script type="module">import {enhanceSelects} from '/static/select_controls.js';
font.append(...Array.from({length:264},(_,i)=>new Option('Font '+String(i).padStart(3,'0'),String(i))));
window.projection=enhanceSelects();window.ready=true;</script>`;
const server = http.createServer((req, res) => {
  if (req.url === "/") {
    res.setHeader("Content-Type", "text/html");
    return res.end(html);
  }
  const name = req.url.replace(/^\/static\//, "");
  if (
    !["controls.css", "select_controls.js", "select_state.js"].includes(name)
  ) {
    res.statusCode = 404;
    return res.end();
  }
  res.setHeader(
    "Content-Type",
    name.endsWith(".js") ? "text/javascript" : "text/css",
  );
  res.end(fs.readFileSync(path.join(root, "src/fiber_bundle/web", name)));
});
(async () => {
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  let browser, app;
  try {
    if (process.env.FIBER_SELECT_ELECTRON) {
      app = await _electron.launch({
        executablePath: process.env.FIBER_SELECT_ELECTRON,
        args: [
          path.join(root, "desktop"),
          "--url",
          `http://127.0.0.1:${server.address().port}/`,
          "--user-data",
          output + "/profile",
        ],
      });
      browser = app.context();
    } else browser = await chromium.launch({ headless: true });
    const page = app ? await app.firstWindow() : await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForFunction(() => window.ready);
    const button = page.getByRole("combobox"),
      list = page.getByRole("listbox");
    await button.click();
    await list.evaluate((el) => {
      window.firstOption = el.firstElementChild;
      el.scrollTop = 1000;
    });
    const before = await list.evaluate((el) => el.scrollTop);
    await page.evaluate(
      () =>
        (document.querySelector("#status").textContent = "Heartbeat refreshed"),
    );
    await page.waitForTimeout(100);
    assert.equal(
      await list.evaluate((el) => el.scrollTop),
      before,
      "unrelated UI refresh must preserve scroll",
    );
    assert.equal(
      await list.evaluate((el) => el.firstElementChild === window.firstOption),
      true,
      "unchanged options must not rebuild",
    );
    const geometry = await page.evaluate(() => ({
      main: document.querySelector("main").scrollTop,
      y: document.querySelector(".select-trigger").getBoundingClientRect().y,
    }));
    const box = await list.boundingBox();
    await page.mouse.move(box.x + 40, box.y + box.height - 3);
    await page.mouse.wheel(0, 580);
    await page.waitForTimeout(200);
    const scrolled = await list.evaluate((el) => el.scrollTop);
    assert.ok(scrolled > before, "wheel must scroll down");
    await page.evaluate(() => {
      const list = document.querySelector(".select-options");
      const row = [...list.children].find(
        (n) =>
          n.getBoundingClientRect().bottom >
          list.getBoundingClientRect().bottom,
      );
      row.dispatchEvent(new PointerEvent("pointermove", { bubbles: true }));
    });
    assert.equal(
      await list.evaluate((el) => el.scrollTop),
      scrolled,
      "hover must not force partially clipped rows into view",
    );
    assert.deepEqual(
      await page.evaluate(() => ({
        main: document.querySelector("main").scrollTop,
        y: document.querySelector(".select-trigger").getBoundingClientRect().y,
      })),
      geometry,
      "dropdown must not move parent viewport",
    );
    await button.press("End");
    assert.ok(
      await list.evaluate(
        (el) =>
          el.lastElementChild.getBoundingClientRect().bottom <=
          el.getBoundingClientRect().top + el.clientTop + el.clientHeight + 1,
      ),
    );
    await button.press("Home");
    assert.ok(await list.evaluate((el) => el.scrollTop < 10));
    await page.evaluate(() => {
      window.events = [];
      font.addEventListener("change", () => events.push("change"));
      font.addEventListener("input", () => events.push("input"));
      font.value = "42";
    });
    await page.waitForTimeout(50);
    assert.equal(await button.innerText(), "Font 042");
    assert.deepEqual(await page.evaluate(() => events), []);
    await button.press("End");
    await button.press("Enter");
    assert.equal(await page.evaluate(() => font.value), "263");
    assert.deepEqual(await page.evaluate(() => events), ["input", "change"]);
    await button.click();
    await list.evaluate((el) => (el.scrollTop = 1000));
    await page.evaluate(() => (font.options[50].textContent = "Renamed font"));
    await page.waitForTimeout(100);
    assert.equal(await list.evaluate((el) => el.scrollTop), 1000);
    assert.equal(
      await list
        .getByRole("option", { name: "Renamed font", exact: true })
        .count(),
      1,
    );
    await page.evaluate(() => (font.disabled = true));
    await page.waitForTimeout(50);
    assert.equal(await button.isDisabled(), true);
    assert.equal(await list.isVisible(), false);
    await page.evaluate(() => (font.disabled = false));
    await page.waitForTimeout(50);
    await page.setViewportSize({ width: 320, height: 650 });
    await button.click();
    const mobile = await list.boundingBox();
    assert.ok(mobile.x >= 0 && mobile.x + mobile.width <= 320);
    assert.equal(await page.title(), "Font dropdown regression");
    assert.deepEqual(errors, []);
    if (output) {
      fs.mkdirSync(output, { recursive: true });
      await page.screenshot({ path: path.join(output, "mobile.png") });
      await button.press("Escape");
      await page.setViewportSize({ width: 1180, height: 820 });
      await page.evaluate(() => {
        document.querySelector("label").style.marginTop = "610px";
        document.querySelector("main").scrollTop = 0;
      });
      await button.click();
      const upward = await list.boundingBox();
      const trigger = await button.boundingBox();
      assert.ok(
        upward.y + upward.height < trigger.y,
        "list opens upward near bottom",
      );
      await button.press("Home");
      await button.press("End");
      assert.deepEqual(
        await button.boundingBox(),
        trigger,
        "keyboard scroll cannot move trigger",
      );
      await page.screenshot({ path: path.join(output, "desktop.png") });
    }
    console.log(
      JSON.stringify({
        state: "PASS",
        groups: 5,
        engine: app ? "Electron" : "Chromium",
        wheel_background_refresh_keyboard_dynamic_options_mobile: true,
        errors,
      }),
    );
  } finally {
    if (app) await app.close();
    else if (browser) await browser.close();
    await new Promise((r) => server.close(r));
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
