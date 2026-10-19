// Synthetic management fixture only: verify current quota controls and responsive copy.
const {
  assert,
  chromium,
  base,
  output,
  path,
  fs,
  write,
  navigate,
} = require("./gui_support.cjs");
(async () => {
  const fixture = JSON.parse(
    fs.readFileSync(
      path.join(process.env.FIBER_CHANNEL_FIXTURE, "fixture.json"),
    ),
  );
  assert.equal(fixture.synthetic, true);
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1180, height: 900 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(base);
    await page.locator("#password").fill("synthetic-gui-acceptance");
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator("#workspace").waitFor({ state: "visible" });
    await navigate(page, "channels");
    await page.locator("#channel-new").click();
    for (const key of [
      "query-limit",
      "model-limit",
      "outbound-limit",
      "download-limit",
      "expert-limit",
      "expert-receipt-limit",
    ]) {
      assert.equal(await page.locator("#channel-" + key).isVisible(), false);
    }
    const card = page.locator(`[data-channel-id="${fixture.ids.feishu}"]`);
    await card.locator("details.channel-intake > summary").click();
    const form = card.locator(".intake-form");
    await form.waitFor({ state: "visible" });
    assert.equal(await form.locator('[name="outbound_limit"]').count(), 0);
    assert.match(await form.innerText(), /回执（次数不限）/);
    assert.match(await form.innerText(), /不设时长额度/);
    await page.screenshot({ path: path.join(output, "unlimited-desktop.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await page.screenshot({ path: path.join(output, "unlimited-mobile.png") });
    assert.deepEqual(errors, []);
    write("unlimited-result", {
      state: "PASS",
      viewports: [1180, 390],
      quota_controls_hidden: true,
      receipt_quota_removed: true,
      page_errors: errors,
      browser: "Browser plugin not available; existing Playwright",
      real_model: false,
      platform_messages: false,
    });
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
