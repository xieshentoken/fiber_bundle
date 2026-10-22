// Browser plugin unavailable: use the project's existing Playwright against synthetic data only.
const { chromium } = require(process.env.PLAYWRIGHT_PATH || "playwright");
const fs = require("node:fs");
const assert = require("node:assert/strict");
const base = process.env.BASE_URL || "http://127.0.0.1:18834";
const out = process.env.EVIDENCE_DIR;
const note = process.env.SYNTHETIC_NOTE;
(async () => {
  assert(out && note, "Synthetic fixture and evidence directory required");
  fs.mkdirSync(out, { recursive: true });
  const b = await chromium.launch({ channel: "chrome" });
  const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  p.setDefaultTimeout(15000);
  const errors = [],
    posts = [],
    checks = [];
  p.on("pageerror", (e) => errors.push(e.message));
  p.on("request", (r) => {
    if (r.url().endsWith("/admin/note-index/update"))
      posts.push(r.postDataJSON());
  });
  await p.addInitScript(() =>
    localStorage.setItem(
      "fiber.appearance.v1",
      JSON.stringify({ defaultTheme: "atlas" }),
    ),
  );
  await p.goto(base + "/#/vaults");
  await p.locator("#atlas-password").fill("synthetic-atlas-only");
  await p.locator(".a-login-form button").click();
  const first = p.locator(".a-vault-card").first();
  await first.getByRole("button", { name: "更新索引", exact: true }).click();
  const panel = p.locator("#vault-note-index");
  await panel.getByText("尚未建立", { exact: true }).waitFor();
  const who = panel.getByRole("combobox", { name: "索引使用的身份" });
  assert.equal(
    await who.locator("option").count(),
    2,
    "Query-only identities eligible, denied identity excluded",
  );
  const original = fs.readFileSync(note);
  await panel.getByRole("button", { name: "更新索引", exact: true }).click();
  await panel.getByText("索引更新完成。", { exact: true }).waitFor();
  await panel.getByText("索引已是最新", { exact: true }).waitFor();
  assert.deepEqual(fs.readFileSync(note), original);
  assert.equal(posts.length, 1);
  checks.push(
    "list-entry",
    "actual-query-only-index-build",
    "note-content-unchanged",
  );
  await p.screenshot({ path: out + "/ready.png", fullPage: true });
  fs.appendFileSync(note, "\n合成测试：更新正文后索引应过期。\n");
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await panel.getByText("笔记已变化，需要更新", { exact: true }).waitFor();
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  await p.route("**/admin/note-index/update", async (route) => {
    await gate;
    await route.continue();
  });
  await panel.getByRole("button", { name: "更新索引", exact: true }).click();
  const busy = panel.getByRole("button", {
    name: "正在更新索引…",
    exact: true,
  });
  await busy.waitFor();
  assert(await busy.isDisabled());
  assert(await who.isDisabled());
  await busy.evaluate((el) => {
    el.click();
    el.click();
  });
  release();
  await panel.getByText("索引已是最新", { exact: true }).waitFor();
  assert.equal(posts.length, 2);
  await p.unroute("**/admin/note-index/update");
  checks.push("edit-stale-update-ready", "duplicate-submit-disabled");
  await p.route("**/admin/note-index/update", (route) =>
    route.fulfill({ status: 409, json: { error: "layout_rebind_required" } }),
  );
  await panel.getByRole("button", { name: "更新索引", exact: true }).click();
  await panel.getByText(/部分已发布笔记已移动/).waitFor();
  assert.equal(posts.length, 3);
  await panel
    .getByRole("button", { name: "核对笔记位置", exact: true })
    .click();
  await p.waitForURL(/tab=organize/);
  checks.push(
    "location-error-guidance",
    "reconciliation-route",
    "no-auto-retry",
  );
  await p.unroute("**/admin/note-index/update");
  const url = p.url().replace("tab=organize", "tab=overview");
  await p.goto(url);
  await panel.getByText("索引已是最新", { exact: true }).waitFor();
  let seen = false,
    releaseStatus;
  const statusGate = new Promise((r) => {
    releaseStatus = r;
  });
  await p.route("**/admin/note-index/status?*", async (route) => {
    if (!seen) {
      seen = true;
      await statusGate;
      await route.fulfill({ json: { state: "stale", notes: 999 } });
    } else await route.continue();
  });
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await p.waitForFunction(
    () => document.querySelector("#vault-note-index button")?.disabled,
  );
  const options = await who
    .locator("option")
    .evaluateAll((els) => els.map((e) => e.value));
  await who.selectOption(options[1]);
  await panel.getByText("索引已是最新", { exact: true }).waitFor();
  releaseStatus();
  await p.waitForTimeout(250);
  assert.equal(await panel.getByText("999", { exact: true }).count(), 0);
  checks.push("late-scope-response-ignored");
  await p.unroute("**/admin/note-index/status?*");
  await p.route("**/admin/note-index/status?*", (route) =>
    route.fulfill({ status: 403, json: { error: "denied" } }),
  );
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await panel.getByText("读取失败", { exact: true }).waitFor();
  assert(
    await panel
      .getByRole("button", { name: "更新索引", exact: true })
      .isDisabled(),
  );
  await p.unroute("**/admin/note-index/status?*");
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await panel.getByText("索引已是最新", { exact: true }).waitFor();
  await p.route("**/admin/note-index/update", (route) => route.abort("failed"));
  const beforeFailure = posts.length;
  await panel.getByRole("button", { name: "更新索引", exact: true }).click();
  await panel.getByText(/更新结果尚未确认/).waitFor();
  await panel.getByText("索引已是最新", { exact: true }).waitFor();
  assert.equal(posts.length, beforeFailure + 1);
  await p.unroute("**/admin/note-index/update");
  checks.push(
    "revoked-status-disables-write",
    "ambiguous-write-read-only-recheck",
  );
  await p.unroute("**/admin/note-index/status?*");
  await p.setViewportSize({ width: 390, height: 844 });
  await p.screenshot({ path: out + "/mobile.png", fullPage: true });
  assert(
    await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    "No horizontal overflow",
  );
  let afterExit = 0;
  await p.route("**/admin/note-index/status?*", async (route) => {
    afterExit++;
    await route.fulfill({
      json: { state: "stale", update: { state: "building" } },
    });
  });
  await panel.getByRole("button", { name: "刷新状态", exact: true }).click();
  await panel.getByText("正在更新索引…", { exact: true }).first().waitFor();
  await p.goto(base + "/#/vaults");
  const beforeExit = afterExit;
  await p.waitForTimeout(2300);
  assert.equal(afterExit, beforeExit);
  assert.deepEqual(errors, []);
  checks.push("mobile-no-overflow", "polling-disposed-on-exit");
  fs.writeFileSync(
    out + "/browser.json",
    JSON.stringify(
      { state: "PASS", checks, posts: posts.length, errors },
      null,
      2,
    ),
  );
  await b.close();
  console.log("ATLAS_VAULT_INDEX_PASS", checks.length);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
