// Synthetic fixture UI only; never point at a real Vault. Fixture under /tmp, no real apply.
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const fs = require("node:fs");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1180, height: 950 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (["error", "warning"].includes(m.type())) errors.push(m.text());
  });
  await page.goto(
    process.env.FIBER_GUI_ACCEPTANCE_URL || "http://127.0.0.1:18858/",
  );
  await page.waitForFunction(() => window.fixture?.ready);
  assert.match(await page.title(), /外部改动核对验证/);
  await page.getByText("批量核对外部改动", { exact: true }).click();
  await page.getByRole("button", { name: "扫描外部改动", exact: true }).click();
  await page
    .getByText("3 项需要核对；扫描未写入笔记或分类。", { exact: true })
    .waitFor();
  for (const select of await page.getByRole("combobox").all())
    assert.equal(await select.inputValue(), "skip");
  assert.equal(await page.evaluate(() => fixture.applies), 0);
  await page
    .getByRole("button", { name: "生成批量核对预览", exact: true })
    .click();
  await page
    .getByText("请先选择需要重新绑定或确认删除的项目。", { exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "采用所有唯一候选位置", exact: true })
    .click();
  await page
    .getByRole("button", { name: "将所有未定位项选为已删除", exact: true })
    .click();
  await page
    .getByRole("button", { name: "生成批量核对预览", exact: true })
    .click();
  let confirm = page.getByRole("button", {
    name: "确认执行本批核对",
    exact: true,
  });
  await confirm.waitFor();
  assert.equal(await confirm.isDisabled(), true);
  assert.equal(
    await page.evaluate(() => fixture.previews.at(-1).items.length),
    2,
  );
  assert.equal(await page.evaluate(() => fixture.applies), 0);
  const target = page.getByRole("textbox", {
    name: /当前位置 03-Resources\/移动/,
  });
  await target.fill("03-Resources/热管理/更新后的路径.md");
  assert.equal(await confirm.count(), 0);
  await page
    .getByRole("button", { name: "生成批量核对预览", exact: true })
    .click();
  await page.getByRole("checkbox", { name: /我已核对位置映射/ }).check();
  await confirm.click();
  await page
    .getByText("部分完成：已完成项不会重复执行", { exact: true })
    .waitFor();
  assert.equal(await page.evaluate(() => fixture.applies), 1);
  await page.screenshot({
    path: "/tmp/fiber-external-reconcile-ui/desktop-partial.png",
    fullPage: true,
  });
  await page.getByRole("checkbox", { name: /我已核对位置映射/ }).check();
  await page
    .getByRole("button", { name: "恢复本批未完成项", exact: true })
    .click();
  await page.getByText("本批核对完成", { exact: true }).waitFor();
  assert.deepEqual(await page.evaluate(() => fixture.remaining), [2, 1]);
  await page.screenshot({
    path: "/tmp/fiber-external-reconcile-ui/desktop-completed.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({
    path: "/tmp/fiber-external-reconcile-ui/mobile-completed.png",
    fullPage: true,
  });
  await page.goto(
    process.env.FIBER_GUI_ACCEPTANCE_URL || "http://127.0.0.1:18858/",
  );
  await page.waitForFunction(() => fixture.ready);
  await page.getByText("批量核对外部改动", { exact: true }).click();
  await page.getByRole("button", { name: "扫描外部改动", exact: true }).click();
  await page
    .getByRole("button", { name: "采用所有唯一候选位置", exact: true })
    .click();
  await page
    .getByRole("button", { name: "生成批量核对预览", exact: true })
    .click();
  await page.getByRole("checkbox", { name: /我已核对位置映射/ }).check();
  await page.evaluate(() => (fixture.valid = false));
  await page
    .getByRole("button", { name: "确认执行本批核对", exact: true })
    .click();
  assert.equal(await page.evaluate(() => fixture.applies), 0);
  assert.deepEqual(errors, []);
  fs.writeFileSync(
    process.env.FIBER_GUI_CHECKPOINT ||
      "/tmp/fiber-external-reconcile-ui/gui-checkpoint.json",
    JSON.stringify(
      {
        state: "SYNTHETIC_GUI_PASS",
        url: process.env.FIBER_GUI_ACCEPTANCE_URL || "http://127.0.0.1:18858/",
        viewports: ["1180x950", "390x844"],
        checks: [
          "page identity",
          "meaningful content",
          "no runtime overlay",
          "console clean",
          "default skip",
          "explicit deletion",
          "preview no apply",
          "edit invalidates preview",
          "confirmation checkbox",
          "partial error",
          "resume remaining only",
          "stale scope cannot apply",
          "no horizontal overflow",
        ],
        errors,
        screenshots: [
          "/tmp/fiber-external-reconcile-ui/desktop-partial.png",
          "/tmp/fiber-external-reconcile-ui/desktop-completed.png",
          "/tmp/fiber-external-reconcile-ui/mobile-completed.png",
        ],
        browser: "Browser plugin not available; existing Playwright",
        real_vault_writes: 0,
      },
      null,
      2,
    ),
  );
  await browser.close();
  console.log("GUI_PASS");
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
