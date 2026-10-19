// Isolated directory-picker acceptance; requires the synthetic deep-directory fixture.
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
  const browser = await chromium.launch(),
    page = await browser.newPage({ viewport: { width: 1180, height: 900 } }),
    errors = [],
    requests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) =>
    requests.push({ method: request.method(), url: request.url() }),
  );
  try {
    await page.goto(base);
    await page.locator("#password").fill("synthetic-gui-acceptance");
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator("#workspace").waitFor({ state: "visible" });
    assert.equal(await page.title(), "Fiber bundle · 本机管理");
    assert.equal(new URL(page.url()).origin, new URL(base).origin);
    await navigate(page, "vaults");
    await page
      .locator("#vault-list .vault-row")
      .first()
      .getByText("管理", { exact: true })
      .click();
    await page
      .getByRole("button", { name: "保存规则", exact: true })
      .first()
      .click();
    const host = page.locator("#vault-save-panel"),
      field = host.getByRole("textbox", {
        name: "共享图片目录：相对路径",
        exact: true,
      }),
      other = host.getByRole("textbox", {
        name: "已整理默认目录：相对路径",
        exact: true,
      }),
      button = host.getByRole("button", {
        name: "共享图片目录：选择目录",
        exact: true,
      });
    await other.fill("keep-unsaved-draft");
    await button.click();
    const dialog = page.locator("dialog.directory-picker");
    await dialog.waitFor({ state: "visible" });
    await dialog
      .locator('details[data-path="06-Attachments"] > summary')
      .click();
    await dialog
      .locator('details[data-path="06-Attachments/00-images"] > summary')
      .click();
    await dialog
      .locator('[data-directory-choice="06-Attachments/00-images/深层目录"]')
      .click();
    await dialog.waitFor({ state: "hidden" });
    assert.equal(await field.inputValue(), "06-Attachments/00-images/深层目录");
    assert.equal(await other.inputValue(), "keep-unsaved-draft");
    assert.equal(
      await button.evaluate((el) => el === document.activeElement),
      true,
    );
    await button.click();
    await dialog.locator('details[data-path="Attachments"] > summary').click();
    await dialog
      .getByRole("button", { name: "加载更多子文件夹", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelectorAll(
          'dialog [data-directory-choice^="Attachments/folder-"]',
        ).length === 110,
    );
    await page.screenshot({ path: path.join(output, "directory-desktop.png") });
    await page.keyboard.press("Escape");
    assert.equal(await field.inputValue(), "06-Attachments/00-images/深层目录");
    await page.setViewportSize({ width: 390, height: 844 });
    await button.click();
    await page.screenshot({ path: path.join(output, "directory-mobile.png") });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    assert.equal(
      await dialog.evaluate(
        (el) => el.getBoundingClientRect().width <= innerWidth,
      ),
      true,
    );
    assert.deepEqual(errors, []);
    assert.equal(
      requests.some((r) => r.method === "PUT"),
      false,
    );
    write("directory-result", {
      state: "PASS",
      url: base,
      viewports: [1180, 390],
      deep_selection: true,
      pagination: 110,
      draft_preserved: true,
      cancel_preserved: true,
      keyboard_focus: true,
      overflow: false,
      page_errors: errors,
      automatic_policy_writes: 0,
      browser: "Playwright; Browser plugin not available",
    });
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
