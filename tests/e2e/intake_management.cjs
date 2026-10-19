// Opt-in synthetic intake acceptance. Requires intake_acceptance_server.py, no real platform.
const {
  assert,
  fs,
  path,
  chromium,
  base,
  output,
  write,
  observe,
  login,
  navigate,
  screenshot,
} = require("./gui_support.cjs");
const { _electron } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const fixture = JSON.parse(
  fs.readFileSync(path.join(process.env.FIBER_CHANNEL_FIXTURE, "fixture.json")),
);
assert(fixture.synthetic);
const result = {
  state: "RUNNING",
  synthetic: true,
  real_model: false,
  real_platform: false,
  checks: [],
  screenshots: [],
  requests: [],
  console: [],
  page_errors: [],
};
let browser, desktop;
const card = (page) =>
  page.locator(`[data-channel-id="${fixture.ids.feishu}"]`);
const intake = (page) => card(page).locator(".channel-intake");
async function open(page) {
  await login(page);
  await navigate(page, "channels");
  await intake(page).locator(":scope > summary").click();
  await intake(page).locator(".intake-form").waitFor({ state: "visible" });
  await card(page)
    .getByText("自动入库已关闭 · 目标库：personal", { exact: true })
    .waitFor();
  assert.equal(await page.title(), "Fiber bundle · 本机管理");
  assert.equal(new URL(page.url()).origin, new URL(base).origin);
}
async function geometry(page) {
  const metrics = await page.evaluate(() => ({
    width: innerWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  assert.equal(metrics.width, metrics.scroll, "No horizontal page overflow");
  assert.equal(
    await intake(page)
      .locator(".intake-form")
      .evaluate((node) => getComputedStyle(node).display),
    "grid",
  );
}
(async () => {
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({
      viewport: { width: 1180, height: 820 },
    });
    observe(page, result);
    await open(page);
    assert.equal(
      await intake(page).locator('[name="enabled"]').isChecked(),
      false,
    );
    assert.equal(
      await intake(page).locator('[name="site"]:checked').count(),
      5,
    );
    await intake(page)
      .getByRole("button", { name: "清空网站", exact: true })
      .click();
    assert.equal(
      await intake(page).locator('[name="site"]:checked').count(),
      0,
    );
    await intake(page)
      .getByRole("button", { name: "全选网站", exact: true })
      .click();
    const form = intake(page).locator(".intake-form");
    await form.locator('[name="custom_domains"]').fill("papers.org");
    await intake(page)
      .getByRole("button", { name: "刷新策略与最近记录", exact: true })
      .click();
    assert.equal(
      await intake(page).locator('[name="custom_domains"]').inputValue(),
      "papers.org",
    );
    result.checks.push(
      "Default off, five site presets, clear/select all, refresh retains draft",
    );
    await intake(page)
      .getByRole("button", { name: "保存自动入库策略", exact: true })
      .click();
    await page
      .getByText("策略已保存；历史消息未回放。入口运行时将处理之后的新消息。", {
        exact: true,
      })
      .waitFor();
    const policy = await page.evaluate(
      async (id) => (await fetch(`/admin/channels/${id}/intake`)).json(),
      fixture.ids.feishu,
    );
    assert.deepEqual(policy.settings.custom_domains, ["papers.org"]);
    assert.equal(policy.settings.enabled, false);
    assert(
      policy.history.some((batch) => batch.batch_id === fixture.history_batch),
    );
    result.checks.push(
      "Save persists policy without replaying history or enabling switch",
    );
    await intake(page)
      .locator('.intake-form [role="combobox"]')
      .first()
      .click();
    await page.getByRole("option", { name: "personal", exact: true }).click();
    result.checks.push(
      "Styled writable-target selector responds to real pointer interaction",
    );
    await screenshot(page, result, "intake-desktop-policy");
    const original = intake(page)
      .getByRole("button", { name: "查看本批原件", exact: true })
      .first();
    await original.click();
    await intake(page).locator(".intake-originals a").first().waitFor();
    const download = page.waitForEvent("download");
    await intake(page).locator(".intake-originals a").first().click();
    assert((await download).suggestedFilename().endsWith(".txt"));
    await intake(page)
      .getByRole("button", {
        name: "核对正文 / 图片 / 字幕并审核",
        exact: true,
      })
      .first()
      .click();
    await intake(page).locator(".intake-review").waitFor();
    const review = intake(page).locator(".intake-review");
    await review
      .locator("img")
      .first()
      .evaluate((node) => node.decode());
    assert.equal(
      await review
        .locator("img")
        .first()
        .evaluate((node) => node.naturalWidth),
      1,
    );
    assert((await review.locator("pre").textContent()).includes("合成段落"));
    await review.locator("textarea").fill("GUI 合成内容已逐段核对");
    for (const checkbox of await review.locator('input[type="checkbox"]').all())
      await checkbox.check();
    await review
      .getByRole("button", { name: "记录人工审核", exact: true })
      .click();
    await review
      .getByText("人工审核已保存，可以确认发布。", { exact: true })
      .waitFor();
    await review
      .getByRole("button", { name: "确认发布到所选知识库", exact: true })
      .click();
    await review.getByText(/^已发布：intake-/).waitFor();
    result.checks.push(
      "Download original retains .txt; literal article review and explicit publication work",
    );
    await screenshot(page, result, "intake-desktop-reviewed");
    const historyRow = intake(page).getByText(
      new RegExp(`选择历史批次 ${fixture.history_batch}`),
      { exact: false },
    );
    await historyRow.locator("input").check();
    await intake(page)
      .getByRole("button", { name: "处理所选批次 / 明确恢复", exact: true })
      .click();
    await page
      .getByText("所选批次已提交；后台按当前授权与累计额度执行。", {
        exact: true,
      })
      .waitFor();
    await page.waitForFunction(
      async ({ id, batch }) =>
        (
          await (await fetch(`/admin/channels/${id}/intake`)).json()
        ).records.some((r) => r.batch_id === batch && r.state === "stored"),
      { id: fixture.ids.feishu, batch: fixture.history_batch },
    );
    result.checks.push(
      "Explicitly selected history archives even when automatic switch is off",
    );
    await geometry(page);
    for (const [name, width, height] of [
      ["mobile", 390, 844],
      ["reflow", 320, 844],
    ]) {
      const phone = await browser.newPage({ viewport: { width, height } });
      observe(phone, result);
      await open(phone);
      await geometry(phone);
      await screenshot(phone, result, `intake-${name}`);
      await intake(phone).locator('[name="material"][value="link"]').uncheck();
      assert.equal(
        await intake(phone)
          .getByText("允许自动处理的网站", { exact: true })
          .isVisible(),
        false,
      );
      await phone.close();
      result.checks.push(
        `${width}px reflow and link-site disclosure respond without clipping`,
      );
    }
    desktop = await _electron.launch({
      executablePath:
        process.env.FIBER_ELECTRON_EXECUTABLE ||
        path.resolve(
          "node_modules/electron/dist/Electron.app/Contents/MacOS/Electron",
        ),
      args: [
        path.resolve("desktop"),
        "--url",
        base,
        "--user-data",
        path.join(output, "desktop-profile"),
      ],
      timeout: 25000,
    });
    const native = await desktop.firstWindow();
    observe(native, result);
    await open(native);
    await intake(native)
      .locator('[name="custom_domains"]')
      .fill("native-synthetic.org");
    await intake(native)
      .getByRole("button", { name: "刷新策略与最近记录", exact: true })
      .click();
    assert.equal(
      await intake(native).locator('[name="custom_domains"]').inputValue(),
      "native-synthetic.org",
    );
    await geometry(native);
    await screenshot(native, result, "intake-electron");
    await intake(native)
      .getByRole("button", { name: "查看本批原件", exact: true })
      .first()
      .click();
    const nativeOriginal = intake(native)
      .locator(".intake-originals a")
      .first();
    await nativeOriginal.waitFor();
    const expected = await nativeOriginal.evaluate(async (node) =>
      Array.from(new Uint8Array(await (await fetch(node.href)).arrayBuffer())),
    );
    const savedPath = path.join(output, "native-original.txt");
    await desktop.evaluate(({ BrowserWindow }, destination) => {
      globalThis.__intakeDownload = new Promise((resolve) => {
        BrowserWindow.getAllWindows()[0].webContents.session.once(
          "will-download",
          (_event, item) => {
            item.setSavePath(destination);
            item.once("done", (_event, state) =>
              resolve({ state, filename: item.getFilename() }),
            );
          },
        );
      });
    }, savedPath);
    await nativeOriginal.click();
    const downloaded = await desktop.evaluate(() =>
      Promise.race([
        globalThis.__intakeDownload,
        new Promise((_resolve, reject) =>
          setTimeout(
            () => reject(Error("Native download did not finish")),
            10000,
          ),
        ),
      ]),
    );
    assert.equal(downloaded.state, "completed");
    assert(downloaded.filename.endsWith(".txt"));
    assert.deepEqual(fs.readFileSync(savedPath), Buffer.from(expected));
    result.checks.push(
      "Native original download uses the authenticated route and saves matching bytes with .txt extension",
    );
    result.checks.push(
      "Independent Electron window renders styled intake controls and retains edits on refresh",
    );
    // Keep the unsaved-draft assertion above. Discard only the synthetic draft
    // before closing so the native unsaved-content dialog cannot block cleanup.
    await intake(native).locator('[name="custom_domains"]').fill("papers.org");
    assert.equal(
      result.page_errors.length,
      0,
      JSON.stringify(result.page_errors),
    );
    assert(
      !result.console.some((entry) => !entry.text.includes("401")),
      JSON.stringify(result.console),
    );
    result.state = "PASS";
  } catch (error) {
    result.state = "FAIL";
    result.error = error.stack;
    throw error;
  } finally {
    write("intake-gui", result);
    if (desktop) {
      // Cleanup only: native will-prevent-unload is a synchronous OS dialog.
      await desktop
        .evaluate(({ dialog }) => {
          dialog.showMessageBoxSync = () => 1;
        })
        .catch(() => {});
      await desktop.close();
    }
    if (browser) await browser.close();
  }
})().catch(() => (process.exitCode = 1));
