// Opt-in acceptance against channel_acceptance_server.py. No real account or model calls.
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
const { createHash } = require("node:crypto");
const fixture = process.env.FIBER_CHANNEL_FIXTURE;
assert(fixture && fixture.startsWith("/private/tmp/"));
const manifest = JSON.parse(
  fs.readFileSync(path.join(fixture, "fixture.json")),
);
assert.equal(manifest.synthetic, true);
const ids = manifest.ids;
const result = {
  state: "RUNNING",
  synthetic: true,
  real_model: false,
  remote_platform_delivery: false,
  browser: "Browser plugin not available; bundled Playwright",
  checks: [],
  screenshots: [],
  page_errors: [],
  console: [],
  requests: [],
};
let browser, desktop;
const card = (page, key) => page.locator(`[data-channel-id="${ids[key]}"]`);
const button = (page, key, action) =>
  card(page, key).locator(`[data-channel-action="${action}"]`);
async function textIs(locator, expected) {
  await locator
    .page()
    .waitForFunction(
      ({ selector, text }) =>
        document.querySelector(selector)?.textContent === text,
      {
        selector: await locator.evaluate(
          (node) =>
            `[data-channel-id="${node.closest("article").dataset.channelId}"] [data-status="${node.dataset.status}"]`,
        ),
        text: expected,
      },
    );
}
async function ready(page) {
  await login(page);
  await navigate(page, "channels");
  await card(page, "legacy").waitFor({ state: "visible" });
  assert.equal(await page.title(), "Fiber bundle · 本机管理");
  assert.equal(new URL(page.url()).origin, new URL(base).origin);
  assert.equal(await page.locator("#page-name").textContent(), "消息入口");
}
async function geometry(page) {
  const data = await page.evaluate(() => ({
    width: innerWidth,
    scroll: document.documentElement.scrollWidth,
    cards: [...document.querySelectorAll(".channel-card")].map((node) => {
      const rect = node.getBoundingClientRect();
      return { left: rect.left, right: rect.right };
    }),
  }));
  assert.equal(data.width, data.scroll, "No horizontal page clipping");
  assert(
    data.cards.every((rect) => rect.left >= 0 && rect.right <= data.width + 1),
  );
  return data;
}
(async () => {
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 1180, height: 820 },
    });
    const page = await context.newPage();
    observe(page, result);
    let accept = true;
    page.on("dialog", (dialog) =>
      accept ? dialog.accept() : dialog.dismiss(),
    );
    await ready(page);
    await textIs(
      card(page, "legacy").locator('[data-status="transport"]'),
      "已连接",
    );
    assert.equal(
      await card(page, "legacy").locator('[data-status="query"]').textContent(),
      "暂不可用",
    );
    assert.equal(
      await card(page, "feishu").locator('[data-status="query"]').textContent(),
      "可用",
    );
    assert.equal(
      await card(page, "login")
        .locator('[data-status="binding"]')
        .textContent(),
      "等待扫码",
    );
    await screenshot(page, result, "channels-desktop");
    await geometry(page);
    result.checks.push(
      "Process, transport and capability shown separately; both platforms visible",
    );

    await card(page, "feishu")
      .getByText("本库查询与模型测试", { exact: true })
      .click();
    const question = card(page, "feishu").getByPlaceholder("输入本库测试问题");
    await question.fill("保留正在编辑的问题");
    await question.focus();
    await page.waitForResponse((response) =>
      response.url().endsWith("/admin/channels/overview"),
    );
    assert.equal(await question.inputValue(), "保留正在编辑的问题");
    assert(await question.evaluate((node) => node === document.activeElement));
    await page.locator("#channel-refresh").click();
    assert.equal(await question.inputValue(), "保留正在编辑的问题");
    result.checks.push(
      "Polling and explicit refresh preserve question draft and polling preserves focus",
    );

    accept = false;
    const removePath = `/admin/channels/${ids.error}/lifecycle`;
    await button(page, "error", "remove").click();
    await page.waitForFunction(
      () =>
        !document.querySelector(
          '[data-channel-action="remove"][aria-busy="true"]',
        ),
    );
    assert.equal(
      result.requests.filter(
        (r) => r.method === "POST" && r.path === removePath,
      ).length,
      0,
    );
    assert(await card(page, "error").isVisible());
    accept = true;
    await button(page, "error", "remove").click();
    await card(page, "error").waitFor({ state: "detached" });
    assert(await page.locator("#channel-archive").isVisible());
    assert(
      !fs.existsSync(
        path.join(
          fixture,
          ".state/message-channel-secrets",
          ids.error + ".json",
        ),
      ),
    );
    result.checks.push(
      "Cancel leaves connection intact; confirmed removal archives metadata and clears private credential",
    );

    await button(page, "feishu", "pause").click();
    await textIs(
      card(page, "feishu").locator('[data-status="process"]'),
      "已停止",
    );
    await textIs(
      card(page, "feishu").locator('[data-status="transport"]'),
      "已断开",
    );
    assert(
      fs.existsSync(
        path.join(
          fixture,
          ".state/message-channel-secrets",
          ids.feishu + ".json",
        ),
      ),
    );
    await button(page, "weixin", "unbind").click();
    await textIs(
      card(page, "weixin").locator('[data-status="binding"]'),
      "未绑定",
    );
    assert(
      !fs.existsSync(
        path.join(
          fixture,
          ".state/configs/hermes-feishu-gui-" + ids.weixin,
          "weixin/accounts/synthetic.json",
        ),
      ),
    );
    assert(
      fs
        .readFileSync(path.join(fixture, "test vault/temp_vault1/a.md"), "utf8")
        .includes("2.4 MPa"),
    );
    result.checks.push(
      "Pause waits for owned process exit and retains credential; Weixin unbind clears login after exit; note retained",
    );

    await button(page, "error", "edit")
      .count()
      .then((count) => assert.equal(count, 0));
    await button(page, "feishu", "edit").click();
    await page.locator("#channel-name").fill("尚未保存的入口名称");
    await page.locator("#channel-refresh").click();
    assert.equal(
      await page.locator("#channel-name").inputValue(),
      "尚未保存的入口名称",
    );
    assert.equal(await page.locator("#channel-secret").inputValue(), "");
    result.checks.push(
      "Status refresh preserves configuration edits and exposes no saved secret",
    );

    // Return a controlled unavailable response; the UI must withdraw its green claims.
    await page.route("**/admin/channels/overview", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: '{"error":"synthetic_unavailable"}',
      }),
    );
    await page.waitForFunction(() =>
      document.querySelector('.channel-status[data-stale="true"]'),
    );
    await page.locator("#channels-panel").scrollIntoViewIfNeeded();
    assert.equal(
      await card(page, "legacy")
        .locator('[data-status="transport"]')
        .textContent(),
      "状态已过期，待刷新",
    );
    await screenshot(page, result, "channels-stale");
    await page.unroute("**/admin/channels/overview");
    await page.locator("#channel-refresh").click();
    await textIs(
      card(page, "legacy").locator('[data-status="transport"]'),
      "已连接",
    );
    result.checks.push(
      "Unavailable refresh removes positive health claims and recovers after explicit refresh",
    );

    await navigate(page, "vaults");
    const observations = result.requests.filter((r) =>
      r.path.endsWith("/channels/overview"),
    ).length;
    await page.waitForTimeout(2300);
    assert.equal(
      result.requests.filter((r) => r.path.endsWith("/channels/overview"))
        .length,
      observations,
    );
    await page.locator("#logout").click();
    await page.locator("#login-panel").waitFor({ state: "visible" });
    assert.equal(await page.locator("#channel-list").textContent(), "");
    result.checks.push(
      "Navigation stops observation; logout clears channel details",
    );
    await context.close();

    const mobile = await browser.newContext({
      viewport: { width: 390, height: 844 },
    });
    const phone = await mobile.newPage();
    observe(phone, result);
    await ready(phone);
    await screenshot(phone, result, "channels-mobile");
    await geometry(phone);
    await card(phone, "weixin").scrollIntoViewIfNeeded();
    await screenshot(phone, result, "channels-mobile-actions");
    assert(await button(phone, "weixin", "remove").isVisible());
    await mobile.close();
    result.checks.push(
      "390px mobile reflow retains readable health and reachable management controls",
    );

    const root = path.resolve(__dirname, "../..");
    desktop = await _electron.launch({
      executablePath:
        process.env.FIBER_GUI_ELECTRON_EXECUTABLE ||
        path.join(
          root,
          "node_modules/electron/dist/Electron.app/Contents/MacOS/Electron",
        ),
      args: [
        path.join(root, "desktop"),
        "--url",
        base,
        "--user-data",
        path.join(output, "desktop-profile"),
      ],
      timeout: 25000,
    });
    const native = await desktop.firstWindow();
    observe(native, result);
    native.on("dialog", (dialog) => dialog.accept());
    await ready(native);
    await button(native, "feishu", "edit").click();
    await native.locator("#channel-name").fill("桌面端未保存的输入");
    await native.locator("#channel-refresh").click();
    assert.equal(
      await native.locator("#channel-name").inputValue(),
      "桌面端未保存的输入",
    );
    await native.locator("#channels-panel").scrollIntoViewIfNeeded();
    await geometry(native);
    const bytes = Buffer.from(
      await desktop.evaluate(async ({ BrowserWindow }) =>
        (await BrowserWindow.getAllWindows()[0].webContents.capturePage())
          .toPNG()
          .toString("base64"),
      ),
      "base64",
    );
    fs.writeFileSync(path.join(output, "channels-electron.png"), bytes);
    result.screenshots.push({
      file: "channels-electron.png",
      native_capture: true,
      sha256: createHash("sha256").update(bytes).digest("hex"),
    });
    result.checks.push(
      "Actual Electron with isolated profile renders health and preserves edits on refresh",
    );
    // Discard this synthetic draft through the normal logout flow before closing.
    await native.locator("#logout").click();
    await native.locator("#login-panel").waitFor({ state: "visible" });
    assert.deepEqual(result.page_errors, []);
    assert(
      result.requests.every(
        (item) =>
          item.status < 400 ||
          (item.status === 401 && item.path === "/admin/session") ||
          (item.status === 503 && item.path === "/admin/channels/overview"),
      ),
      "Only initial signed-out checks and the injected unavailable response may fail",
    );
    const unexpected = result.console.filter(
      (item) =>
        !item.text.includes("503 (Service Unavailable)") &&
        !item.text.includes("401 (Unauthorized)"),
    );
    assert.deepEqual(unexpected, []);
    assert.equal(
      result.requests.filter(
        (r) =>
          r.method === "POST" &&
          /\/(check|pair|login|start|test)$/.test(r.path) &&
          r.path.startsWith("/admin/channels/"),
      ).length,
      0,
    );
    result.state = "PASS";
  } catch (error) {
    result.state = "FAIL";
    result.error = error.stack;
    process.exitCode = 1;
  } finally {
    await desktop?.close();
    await browser?.close();
    write("channel-management", result);
    console.log(
      JSON.stringify({
        state: result.state,
        checks: result.checks.length,
        error: result.error,
      }),
    );
  }
})();
