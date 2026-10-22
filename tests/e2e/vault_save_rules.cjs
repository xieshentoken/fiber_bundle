// Isolated actual save-rule editor; no real Vault or mutation endpoint is used.
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL || "http://127.0.0.1:18854";
const out =
  process.env.FIBER_GUI_EVIDENCE || "/tmp/fiber-save-rule-ui-20261017";
assert.equal(new URL(base).hostname, "127.0.0.1");
assert(+new URL(base).port >= 18000 && +new URL(base).port <= 19000);
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1180, height: 1000 },
  });
  const errors = [],
    checks = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (["error", "warning"].includes(m.type())) errors.push(m.text());
  });
  const legacy = page.getByRole("checkbox", {
    name: "确认 MOC 关联后准备归档",
    exact: true,
    includeHidden: true,
  });
  const help = page.locator("#vault-moc-archive-help");
  async function open(version, archive = "off") {
    await page.goto(`${base}/?version=${version}&archive=${archive}`);
    await page.waitForFunction(() => window.fixture?.ready);
    assert.match(await page.title(), /Fiber bundle · 保存规则验证/);
    await page
      .getByRole("heading", { name: "L2_vault · 保存规则", exact: true })
      .waitFor();
  }
  async function select(name, value) {
    await page.getByRole("combobox", { name, exact: true }).click();
    await page.getByRole("option", { name: value, exact: true }).click();
  }
  async function unchangedPreview() {
    await page
      .getByRole("button", { name: "预览保存规则", exact: true })
      .click();
    await page.waitForFunction(() => window.fixture.previews.length === 1);
    const state = await page.evaluate(() => window.fixture);
    assert.deepEqual(state.previews[0].settings, state.settings);
    assert.equal(state.previews[0].expected_revision, 7);
    assert.equal(state.saves, 0);
  }
  for (const value of ["off", "preview", "automatic"]) {
    await open(2, value);
    assert.equal(await legacy.isVisible(), false);
    assert.equal(await legacy.isDisabled(), true);
    assert.equal(await help.isVisible(), true);
    for (const text of [
      "关闭：仅建立 MOC 关联",
      "仅准备预览：生成归档预览",
      "自动执行（普通笔记）：确认关联后",
    ])
      assert((await help.innerText()).includes(text));
    assert.equal(
      await page
        .locator('select[aria-label="确认 MOC 后归档方式"]')
        .inputValue(),
      value,
    );
    assert.match(
      await page
        .getByRole("combobox", { name: "确认 MOC 后归档方式", exact: true })
        .getAttribute("aria-describedby"),
      /vault-moc-archive-help/,
    );
    await unchangedPreview();
    checks.push(`v2-${value}-hidden-legacy-description-and-identical-payload`);
    await select("目录规则版本", "兼容现有规则");
    assert.equal(await legacy.isVisible(), true);
    assert.equal(await legacy.isEnabled(), true);
    await legacy.check();
    await select("目录规则版本", "统一知识库目录");
    assert.equal(await legacy.isVisible(), false);
    assert.equal(
      await page
        .locator('select[aria-label="确认 MOC 后归档方式"]')
        .inputValue(),
      value,
    );
    await select("目录规则版本", "兼容现有规则");
    assert.equal(await legacy.isChecked(), true);
  }
  await open(1);
  assert.equal(await legacy.isVisible(), true);
  assert.equal(await legacy.isChecked(), true);
  assert.equal(await help.isVisible(), false);
  await unchangedPreview();
  checks.push(
    "v1-original-setting-visible-and-identical-payload; switching-preserves-drafts",
  );
  await open(2);
  const screenshots = [];
  for (const width of [1180, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const theme of ["cursor", "oracle", "tesla", "clay"]) {
      await page.evaluate(
        (theme) => (document.documentElement.dataset.theme = theme),
        theme,
      );
      await help.scrollIntoViewIfNeeded();
      assert(await help.isVisible());
      const preset = await page
        .getByRole("button", { name: "填入统一库目录预设", exact: true })
        .boundingBox();
      const trigger = await page
        .getByRole("combobox", {
          name: "确认 MOC 后归档方式",
          exact: true,
        })
        .boundingBox();
      assert(preset.height <= trigger.height * 1.5);
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      if (theme === "clay") {
        const filename = path.join(out, `save-rules-${width}.png`);
        await page.screenshot({ path: filename, fullPage: false });
        screenshots.push(filename);
      }
    }
  }
  checks.push("desktop-and-mobile-theme-layout-no-horizontal-overflow");
  assert.deepEqual(errors, []);
  const result = {
    state: "PASS",
    browser: "Browser plugin not available; installed Playwright",
    url: base,
    viewports: [1180, 390],
    checks,
    errors,
    screenshots,
    isolated_fixture: true,
    real_configuration_writes: 0,
  };
  fs.writeFileSync(
    path.join(out, "gui-result.json"),
    JSON.stringify(result, null, 2),
  );
  console.log(result);
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
  process.exit(1);
});
