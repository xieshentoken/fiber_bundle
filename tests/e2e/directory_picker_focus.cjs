// Synthetic instance only: auto-reveal, pagination, missing paths and cancelled reads.
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
  const errors = [],
    requests = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) =>
    requests.push({ method: r.method(), url: r.url() }),
  );
  try {
    await page.goto(base);
    await page.locator("#password").fill("synthetic-gui-acceptance");
    await page.locator("#login-form button").click();
    await page.locator("#workspace").waitFor({ state: "visible" });
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
      dialog = page.locator("dialog.directory-picker"),
      status = dialog.locator(".directory-location-status");
    const field = host.getByRole("textbox", {
        name: "共享图片目录：相对路径",
        exact: true,
      }),
      other = host.getByRole("textbox", {
        name: "已整理默认目录：相对路径",
        exact: true,
      });
    const open = () =>
      host
        .getByRole("button", { name: "共享图片目录：选择目录", exact: true })
        .click();
    const deep = "06-Attachments/00-images/深层目录";
    await other.fill("keep-unsaved-draft");
    await field.fill(deep);
    await open();
    await status.filter({ hasText: "已定位当前目录" }).waitFor();
    for (const name of ["06-Attachments", "06-Attachments/00-images"])
      assert.equal(
        await dialog
          .locator(`details[data-path="${name}"]`)
          .evaluate((n) => n.open),
        true,
      );
    const selected = dialog.locator(`[data-directory-choice="${deep}"]`);
    assert.equal(
      await selected.evaluate((n) => n === document.activeElement),
      true,
    );
    assert.equal(await selected.getAttribute("aria-pressed"), "true");
    await page.screenshot({
      path: path.join(output, "auto-reveal-desktop.png"),
      animations: "disabled",
    });
    await page.keyboard.press("Escape");
    assert.equal(await field.inputValue(), deep);
    assert.equal(await other.inputValue(), "keep-unsaved-draft");
    assert.equal(
      await host
        .getByRole("button", { name: "共享图片目录：选择目录", exact: true })
        .evaluate((n) => n === document.activeElement),
      true,
    );
    // Locate the 110th sibling automatically, without changing an unrelated field.
    await field.fill("Attachments/folder-109");
    await open();
    await status.filter({ hasText: "已定位当前目录" }).waitFor();
    assert.equal(
      await dialog
        .locator('[data-directory-choice="Attachments/folder-109"]')
        .evaluate((n) => n === document.activeElement),
      true,
    );
    await page.keyboard.press("Escape");
    // Every role uses the same picker, including v2 fields.
    for (const name of [
      "未分类笔记目录（兼容规则包含专家 Draft）",
      "已整理默认目录",
      "MOC 默认目录",
      "完整专家 Draft 目录",
      "附件根目录（不索引正文）",
      "非图片原件与附件目录",
    ]) {
      const input = host.getByRole("textbox", {
        name: name + "：相对路径",
        exact: true,
      });
      await input.fill(deep);
      await host
        .getByRole("button", { name: name + "：选择目录", exact: true })
        .click();
      await status.filter({ hasText: "已定位当前目录" }).waitFor();
      await page.keyboard.press("Escape");
      assert.equal(await input.inputValue(), deep);
    }
    await field.fill("06-Attachments/00-images/待创建");
    await open();
    await status.filter({ hasText: "不存在或不在可访问范围" }).waitFor();
    await page.keyboard.press("Escape");
    assert.equal(await field.inputValue(), "06-Attachments/00-images/待创建");
    let fail = true;
    await page.route("**/directories?**", async (route) => {
      if (
        fail &&
        new URL(route.request().url()).searchParams.get("relative_parent") ===
          "06-Attachments"
      ) {
        fail = false;
        await route.fulfill({
          status: 503,
          json: { detail: "synthetic_read_failure" },
        });
      } else await route.continue();
    });
    await field.fill(deep);
    await open();
    await status.filter({ hasText: "目录定位未完成" }).waitFor();
    assert.equal(await field.inputValue(), deep);
    await dialog
      .getByRole("button", { name: "刷新目录并定位", exact: true })
      .click();
    await status.filter({ hasText: "已定位当前目录" }).waitFor();
    await page.keyboard.press("Escape");
    await page.unroute("**/directories?**");
    // Old in-flight response is released only after a different field has opened.
    let release, seen;
    const waitSeen = new Promise((r) => (seen = r)),
      delayed = new Promise((r) => (release = r));
    let hold = true;
    await page.route("**/directories?**", async (route) => {
      if (hold) {
        hold = false;
        seen();
        await delayed;
      }
      await route.continue();
    });
    await open();
    await waitSeen;
    await page.keyboard.press("Escape");
    await host
      .getByRole("button", { name: "已整理默认目录：选择目录", exact: true })
      .click();
    await status.filter({ hasText: "已定位当前目录" }).waitFor();
    release();
    await page.waitForTimeout(150);
    assert.match(await dialog.locator("h3").innerText(), /已整理默认目录/);
    assert.equal(await dialog.isVisible(), true);
    await page.keyboard.press("Escape");
    await page.unroute("**/directories?**");
    await page.setViewportSize({ width: 390, height: 844 });
    await open();
    await status.filter({ hasText: "已定位当前目录" }).waitFor();
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await page.screenshot({
      path: path.join(output, "auto-reveal-mobile.png"),
      animations: "disabled",
    });
    assert.deepEqual(errors, []);
    assert.equal(
      requests.some((r) => r.method === "PUT"),
      false,
    );
    write("directory-focus-result", {
      state: "PASS",
      checks: [
        "automatic deep expansion and focus",
        "pagination finds 110th sibling",
        "all seven directory roles",
        "missing path preserves input",
        "failure and retry",
        "cancel and stale response isolation",
        "draft and keyboard focus preserved",
        "1180/390px no overflow",
      ],
      page_errors: errors,
      policy_writes: 0,
      real_models: false,
    });
  } finally {
    await page.screenshot({
      path: path.join(output, "last.png"),
      animations: "disabled",
    });
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
