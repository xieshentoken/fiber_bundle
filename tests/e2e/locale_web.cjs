// Synthetic local instance only. No model calls, MOC writes, or archiving.
const base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
if (!base || !out)
  throw Error("Set synthetic backend URL and evidence directory");
const endpoint = new URL(base);
if (
  endpoint.hostname !== "127.0.0.1" ||
  +endpoint.port < 18000 ||
  +endpoint.port > 19000
)
  throw Error("Synthetic test port required");
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const fs = require("node:fs"),
  path = require("node:path"),
  assert = require("node:assert/strict");
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
      viewport: { width: 1440, height: 900 },
    }),
    errors = [],
    writes = [],
    han = {};
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/admin/") && r.method() !== "GET")
      writes.push(new URL(r.url()).pathname);
  });
  await page.goto(base);
  await page
    .locator("[data-language-control]")
    .first()
    .selectOption("en", { force: true });
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button[type=submit]").click();
  await page.locator("#workspace").waitFor({ state: "visible" });
  const tabs = await page
    .locator("[data-tab]")
    .evaluateAll((ns) => ns.map((n) => n.dataset.tab));
  for (const tab of tabs) {
    await page.evaluate(
      (tab) => document.querySelector(`[data-tab="${tab}"]`).click(),
      tab,
    );
    await page.locator("#page-loading").waitFor({ state: "hidden" });
    await page.waitForTimeout(80);
    han[tab] = await page
      .locator("#" + tab + "-panel")
      .evaluate((n) =>
        [...n.querySelectorAll("*")]
          .filter(
            (x) =>
              x.getClientRects().length &&
              x.childElementCount === 0 &&
              /[\u4e00-\u9fff]/.test(x.textContent) &&
              !x.closest("pre"),
          )
          .map((x) => [
            x.tagName,
            x.id,
            x.className,
            x.textContent.slice(0, 250),
          ]),
      );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      tab + " overflow",
    );
    await page.screenshot({ path: path.join(out, `C-${tab}-en.png`) });
  }
  await page.evaluate(() =>
    document.querySelector("[data-tab=models]").click(),
  );
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page.locator("#model-config-tab").click();
  await page.locator("#models-add").click();
  await page.locator("#model-wizard").waitFor({ state: "visible" });
  const providerLabels = await page
    .locator("#model-wizard-provider option")
    .allTextContents();
  assert(
    providerLabels.every((text) => !/[\u4e00-\u9fff]/.test(text)),
    "Built-in provider options must localize",
  );
  await page.locator("#model-wizard-api_key").fill("synthetic-not-a-real-key");
  await page.evaluate(async () => {
    const m = await import("/static/i18n/index.js");
    m.setLocale("zh-CN");
    m.setLocale("en");
  });
  assert.equal(
    await page.locator("#model-wizard-api_key").inputValue(),
    "synthetic-not-a-real-key",
    "Locale change keeps unsubmitted draft",
  );
  page.once("dialog", (dialog) => dialog.accept());
  await page
    .locator("#model-wizard")
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  await page.evaluate(() => document.querySelector("[data-tab=home]").click());
  await page.locator("#home-content .cost-facts").waitFor({ state: "visible" });
  await page.locator("#home-content .arch-card > .arch-head").count();
  assert(
    await page
      .locator("[data-tab=home]")
      .evaluate(
        (n) =>
          n.getBoundingClientRect().top >
          document.getElementById("command-open").getBoundingClientRect()
            .bottom,
      ),
  );
  for (const theme of ["oracle", "tesla", "cursor", "clay", "dark"]) {
    await page.locator("#theme-toggle").click();
    await page.locator(`[data-theme-choice=${theme}]`).click();
    await page.keyboard.press("Escape");
    await page.mouse.move(1100, 750);
    await page.locator("#theme-toggle").blur();
    await page.waitForTimeout(450);
    assert.equal(
      await page
        .locator(".brand-name")
        .evaluate((n) => getComputedStyle(n).color),
      "rgb(0, 0, 0)",
    );
    assert(
      await page
        .locator(".brand-name")
        .evaluate(
          (n) =>
            n.getBoundingClientRect().right <=
            n.closest(".brand").getBoundingClientRect().right,
        ),
    );
    await page.screenshot({ path: path.join(out, `C-home-${theme}-en.png`) });
  }
  fs.writeFileSync(path.join(out, "C-han.json"), JSON.stringify(han, null, 2));
  await page.keyboard.press("Meta+k");
  await page.locator("#command-palette input").fill("vault");
  assert(
    (await page.locator("#command-palette .command-list button").count()) > 0,
  );
  await page.keyboard.press("Escape");
  for (const width of [1180, 1024, 760, 390]) {
    await page.setViewportSize({ width, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      "home " + width,
    );
  }
  fs.writeFileSync(
    path.join(out, "C-results.json"),
    JSON.stringify({ errors, writes, han, pass: errors.length === 0 }, null, 2),
  );
  console.log(JSON.stringify({ errors, writes, han }, null, 2));
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, ["/admin/login"]);
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
