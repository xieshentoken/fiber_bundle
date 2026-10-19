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
  });
  const errors = [],
    writes = [],
    reads = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    const path = new URL(r.url()).pathname;
    if (path.startsWith("/admin/"))
      (r.method() === "GET" ? reads : writes).push(path);
  });
  await page.goto(base);
  await page.locator("#login-panel").waitFor({ state: "visible" });
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button[type=submit]").click();
  await page.locator("#workspace").waitFor({ state: "visible" });
  await page.locator("[data-tab=vaults]").click();
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page.locator("#workspace-vaults-save-tab").click();
  const select = page.locator(
    "#workspace-vaults-save-view .vault-scope-picker select",
  );
  await select.locator("option").nth(2).waitFor({ state: "attached" });
  const ids = await select
    .locator("option")
    .evaluateAll((ns) => ns.map((n) => n.value).filter(Boolean));
  assert.equal(ids.length, 2);
  await select.selectOption(ids[0], { force: true });
  await page
    .locator("#vault-save-panel .form-section")
    .nth(1)
    .waitFor({ state: "visible" });
  const input = page
    .locator("#vault-save-panel .directory-field > input")
    .first();
  await input.fill("Inbox/keep-unsaved");
  page.once("dialog", (d) => d.dismiss());
  await select.selectOption(ids[1], { force: true });
  await page.waitForFunction(
    ([id]) =>
      document.querySelector("#workspace-vaults-save-view select").value === id,
    [ids[0]],
  );
  assert.equal(await input.inputValue(), "Inbox/keep-unsaved");
  assert.equal(writes.filter((p) => p !== "/admin/login").length, 0);
  await page.evaluate(async () => {
    const m = await import("/static/i18n/index.js");
    m.setLocale("en");
  });
  assert.equal(await input.inputValue(), "Inbox/keep-unsaved");
  await page.evaluate(async () => {
    const m = await import("/static/i18n/index.js");
    m.setLocale("zh-CN");
  });
  page.once("dialog", (d) => d.accept());
  await select.selectOption(ids[1], { force: true });
  await page.waitForFunction(
    (id) =>
      document
        .querySelector("#vault-save-panel > h3")
        ?.textContent.includes("supplier"),
    ids[1],
  );
  await page.screenshot({
    path: path.join(out, "B-save-rules-zh.png"),
    fullPage: true,
  });
  await page.screenshot({ path: path.join(out, "B-save-rules-top.png") });
  let release, parked;
  const captured = new Promise((r) => {
    parked = r;
  });
  await page.route(
    "**/admin/vault-catalog/" + ids[0] + "/save-policy",
    async (route) => {
      const response = await route.fetch();
      await new Promise((resolve) => {
        release = resolve;
        parked();
      });
      await route.fulfill({ response });
    },
  );
  await select.selectOption(ids[0], { force: true });
  await page.waitForFunction(() =>
    document
      .querySelector("#vault-save-panel")
      .textContent.includes("正在读取"),
  );
  await captured;
  await select.selectOption(ids[1], { force: true });
  await page.waitForFunction(() =>
    document
      .querySelector("#vault-save-panel > h3")
      ?.textContent.includes("supplier"),
  );
  assert(release);
  release();
  await page.waitForTimeout(100);
  assert(
    (await page.locator("#vault-save-panel > h3").innerText()).includes(
      "supplier",
    ),
  );
  await page.unroute("**/admin/vault-catalog/" + ids[0] + "/save-policy");
  await page.locator("#workspace-vaults-layout-tab").click();
  await page
    .locator("#workspace-vaults-layout-view .vault-scope-picker select")
    .selectOption(ids[0], { force: true });
  await page
    .locator("#vault-placement-panel h3")
    .first()
    .waitFor({ state: "visible" });
  assert.equal(writes.filter((p) => p !== "/admin/login").length, 0);
  await page.route("**/admin/organization?**", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.jobs = [
      {
        id: "synthetic-ui-preview",
        maintenance: true,
        mode: "quick",
        state: "preview",
        stage: "planning",
        items: [
          {
            entity: "note:synthetic",
            title: "收件箱",
            path: "Inbox/synthetic.md",
            before: [],
            after: [],
            add: [],
            remove: [],
            evaluated: true,
            selected: [],
            state: "planned",
            status: "review",
            review_reason: "no_matching_moc",
          },
        ],
        new_mocs: [],
        plan_sha256: "a".repeat(64),
      },
    ];
    await route.fulfill({ response, json: body });
  });
  await page.locator("[data-tab=graph]").click();
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page
    .locator("#graph-client")
    .selectOption({ label: "G2 合成资料身份" }, { force: true });
  await page
    .locator('#graph-vault option[value=\"' + ids[0] + '\"]')
    .waitFor({ state: "attached" });
  await page.locator("#graph-vault").selectOption(ids[0], { force: true });
  await page
    .locator(
      "#organization-maintenance [data-maintenance-job=synthetic-ui-preview]",
    )
    .waitFor({ state: "visible" });
  assert.equal(await page.locator(".organization-workspace > h2").count(), 0);
  assert.equal(await page.locator("#organization-maintenance > h3").count(), 0);
  await page.screenshot({
    path: path.join(out, "B-graph-zh.png"),
    fullPage: true,
  });
  await page
    .locator("#organization-maintenance .task-row-summary")
    .waitFor({ state: "visible" });
  await page.evaluate(async () => {
    const m = await import("/static/i18n/index.js");
    m.setLocale("en");
  });
  assert.equal(
    await page
      .locator("#organization-maintenance .task-row-summary strong")
      .innerText(),
    "收件箱",
    "Raw note title must not translate",
  );
  assert(
    (
      await page
        .locator("#organization-maintenance .record-heading")
        .first()
        .innerText()
    ).includes("Quick organization"),
  );
  assert(
    await page
      .locator("#organization-maintenance .task-actions input[type=checkbox]")
      .first()
      .evaluate((n) => n.getBoundingClientRect().width >= 16),
  );
  assert.deepEqual(errors, []);
  fs.writeFileSync(
    path.join(out, "B-results.json"),
    JSON.stringify(
      {
        pass: true,
        errors,
        writes,
        reads,
        checks: [
          "direct scope selection in both tabs",
          "cancel scope change preserves draft",
          "locale preserves rule draft",
          "late response cannot replace newer scope",
          "page entry makes no business writes",
          "duplicate organization headings removed",
        ],
      },
      null,
      2,
    ),
  );
  console.log({ pass: true, errors, writes });
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
