// Isolated read-only fixture. No production ports, credentials, or model requests.
const {
  assert,
  chromium,
  base,
  output,
  path,
  write,
  navigate,
} = require("./gui_support.cjs");
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1180, height: 900 },
  });
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let identity = "fixture-before";
  await page.route("**/health", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.build = { ...body.build, build_id: identity };
    await route.fulfill({ response, json: body });
  });
  await page.route("**/automatic-jobs", async (route) => {
    const vault = new URL(route.request().url()).pathname.split("/")[3];
    await route.fulfill({
      json: [
        {
          id: "ux-auto-review",
          vault_id: vault,
          kind: "document",
          state: "needs_review",
          phase: "review",
          reason: "等待核对",
        },
      ],
    });
  });
  try {
    console.log("load");
    await page.goto(base);
    await page.locator("#password").fill("synthetic-gui-acceptance");
    await page.locator("#login-form button").click();
    await page.locator("#workspace").waitFor({ state: "visible" });
    console.log("login");
    await navigate(page, "home");
    console.log("home");
    const task = page.locator('#home-content [data-task-id="ux-auto-review"]');
    await task.waitFor();
    console.log("task");
    await task.click();
    await page
      .locator('#vault-save-panel [data-task-id="ux-auto-review"]')
      .waitFor();
    assert.equal(
      await page
        .locator('#vault-save-panel [data-task-id="ux-auto-review"]')
        .evaluate((n) => n === document.activeElement),
      true,
    );
    identity = "fixture-after";
    await page.locator("#refresh").click();
    await page
      .locator(".ui-update-notice")
      .waitFor({ state: "visible", timeout: 15000 });
    await navigate(page, "settings");
    await page.locator("[name=cjkSize]").selectOption("18");
    page.once("dialog", (dialog) => dialog.dismiss());
    await page.locator(".ui-update-notice button").click();
    assert.equal(await page.locator("[name=cjkSize]").inputValue(), "18");
    await page.locator("#appearance-cancel").click();
    await page.route("**/admin/system/fonts", (route) =>
      route.fulfill({
        json: {
          supported: true,
          source: "system",
          fonts: [
            {
              family: "Alpha Font",
              postscriptName: "AlphaFont",
              style: "Regular",
            },
            {
              family: "Beta Font",
              postscriptName: "BetaFont",
              style: "Regular",
            },
          ],
        },
      }),
    );
    await page.locator("#font-refresh").click();
    await page.waitForFunction(() =>
      document.getElementById("font-status").textContent.includes("已读取 2"),
    );
    await page.locator('[aria-label="筛选中文字体"]').fill("Alpha");
    assert.equal(
      await page.locator("[name=cjkFont] optgroup option").count(),
      1,
    );
    await page.locator("[name=cjkFont]").selectOption({ label: "Alpha Font" });
    await page.locator('[aria-label="筛选中文字体"]').fill("Beta");
    assert.match(await page.locator("[name=cjkFont]").inputValue(), /Alpha/);
    await page.locator("#appearance-cancel").click();
    await navigate(page, "models");
    assert.equal(await page.locator("#model-query-view").isVisible(), true);
    await page.locator("#query-question").fill("保留查询草稿");
    await page.locator("#model-config-tab").click();
    assert.equal(await page.locator("#models-catalog").isVisible(), true);
    await page.locator("#model-config-tab").press("ArrowLeft");
    assert.equal(
      await page.locator("#query-question").inputValue(),
      "保留查询草稿",
    );
    await page.locator("#query-question").fill("");
    await navigate(page, "imports");
    await page.locator('[data-engine="ordinary"]').click();
    assert.ok(
      await page
        .locator("#import-files")
        .evaluate(
          (n) =>
            n.getBoundingClientRect().top <
            document.getElementById("import-vault").getBoundingClientRect().top,
        ),
    );
    await page.screenshot({
      animations: "disabled",
      path: path.join(output, "batch2.png"),
    });
    write("batch2", {
      state: "PASS",
      checks: [
        "font filtering preserves selection",
        "model tabs preserve drafts and keyboard switching",
        "file selection precedes target",
      ],
      real_model: false,
    });
    await page.screenshot({
      animations: "disabled",
      path: path.join(output, "batch1.png"),
    });
    await navigate(page, "home");
    await page.locator(".home-system-details").waitFor();
    assert.equal(
      await page.locator(".home-system-details").getAttribute("open"),
      null,
    );
    await page.locator(".home-system-details > summary").click();
    assert.equal(
      await page.locator(".home-system-details").evaluate((n) => n.open),
      true,
    );
    await page.locator(".home-system-details > summary").click();
    // Compare the same page and content in all three themes and two widths.
    identity = "fixture-before";
    await page.locator("#refresh").click();
    for (const theme of ["cursor", "oracle", "tesla"]) {
      await page.locator('[aria-controls="theme-picker"]').first().click();
      await page.locator(`[data-theme-choice="${theme}"]`).click();
      await page.locator("#theme-picker").waitFor({ state: "hidden" });
      for (const width of [1180, 390]) {
        await page.locator("#sidebar").evaluate(n => { n.scrollTop = 0; });
        await page.setViewportSize({ width, height: 900 });
        await page.waitForFunction(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        );
        await page.waitForTimeout(400);
        await page.screenshot({
          animations: "disabled",
          path: path.join(output, `home-${theme}-${width}.png`),
          fullPage: true,
        });
      }
      await page.setViewportSize({ width: 1180, height: 900 });
    }
    await page.locator('[aria-controls="theme-picker"]').first().click();
    await page.waitForTimeout(400);
    await page.screenshot({
      animations: "disabled",
      path: path.join(output, "themes.png"),
    });
    await page.keyboard.press("Escape");
    await navigate(page, "backend");
    assert.equal(
      await page.locator(".component-details").evaluate((n) => n.open),
      false,
    );
    assert.equal(
      await page.locator(".backend-danger").evaluate((n) => n.open),
      false,
    );
    await page.screenshot({
      animations: "disabled",
      path: path.join(output, "backend.png"),
    });
    write("batch3", {
      state: "PASS",
      checks: [
        "architecture expands without navigation",
        "three themes at 1180 and 390 without horizontal overflow",
        "component details and stop controls separately folded",
      ],
      real_model: false,
    });
    assert.deepEqual(errors, []);
    write("batch1", {
      state: "PASS",
      checks: [
        "automatic scope opens and focuses actual task",
        "backend version change offers reload",
        "no page errors",
      ],
      real_model: false,
    });
  } finally {
    await page.screenshot({
      animations: "disabled",
      path: path.join(output, "last.png"),
    });
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
