/* global process, URL, console */
// Opt-in synthetic fixture: stale organization reads must not hide index recovery.
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const fs = require("node:fs"),
  assert = require("node:assert/strict");
const T = process.env.FIBER_GUI_EVIDENCE,
  base = new URL(process.env.FIBER_GUI_ACCEPTANCE_URL);
assert(
  base.hostname === "127.0.0.1" && +base.port >= 18000 && +base.port <= 19000,
);
assert(T);
fs.mkdirSync(T, { recursive: true });
const syntheticNote = process.env.FIBER_SYNTHETIC_NOTE;
assert(
  /^\/(private\/)?tmp\/fiber-[^/]+\/fixture\/test vault\/temp_vault1\/欢迎\.md$/.test(
    syntheticNote,
  ),
);
(async () => {
  const b = await chromium.launch({ channel: "chrome" });
  const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  p.setDefaultTimeout(15000);
  const errors = [];
  p.on("console", (m) => {
    if (
      m.type() === "error" &&
      !/401 \(Unauthorized\)|409 \(Conflict\)/.test(m.text())
    )
      errors.push(m.text());
  });
  p.on("pageerror", (e) => errors.push(e.message));
  await p.addInitScript(() =>
    localStorage.setItem(
      "fiber.appearance.v1",
      JSON.stringify({ defaultTheme: "atlas" }),
    ),
  );
  await p.goto(base.href + "#/graph");
  await p.locator("#atlas-password").fill("synthetic-atlas-only");
  await p.locator(".a-login-form button").click();
  await p.locator(".a-page[data-page=graph]").waitFor();
  const vaults = await (
      await p.request.get(base.origin + "/admin/vaults")
    ).json(),
    clients = await (
      await p.request.get(base.origin + "/admin/clients")
    ).json(),
    session = await (
      await p.request.get(base.origin + "/admin/session")
    ).json();
  const selected = vaults.find((v) => v.name === "personal");
  await p
    .getByRole("combobox", { name: "知识库", exact: true })
    .selectOption(selected.id);
  const note = syntheticNote;
  let r = await p.request.post(base.origin + "/admin/note-index/update", {
    headers: { Origin: base.origin, "X-CSRF-Token": session.csrf },
    data: { client_id: clients[0].id, vault_id: selected.id },
  });
  assert.equal(r.status(), 200, await r.text());
  fs.appendFileSync(note, "\n合成测试：修改正文以验证索引过期。\n");
  const noteBytes = fs.readFileSync(note);
  let blocked = true;
  await p.route("**/admin/organization?*", async (route) => {
    if (blocked)
      await route.fulfill({
        status: 409,
        json: { error: "note_index_update_required" },
      });
    else await route.continue();
  });
  await p.route("**/admin/note-index/update", async (route) => {
    const response = await route.fetch();
    if (response.status() === 200) blocked = false;
    await route.fulfill({ response });
  });
  await p.reload();
  await p.getByText(/归类状态读取失败/).waitFor();
  const button = p.getByRole("button", {
    name: "更新此知识库索引",
    exact: true,
  });
  const visible = await button.isVisible();
  await p.screenshot({
    path:
      T +
      (!process.env.REPRODUCE_BEFORE_FIX
        ? "/after-stale.png"
        : "/before-stale.png"),
    fullPage: true,
  });
  if (!!process.env.REPRODUCE_BEFORE_FIX) {
    assert.equal(visible, false);
    fs.writeFileSync(
      T + "/before.json",
      JSON.stringify({
        state: "REPRODUCED",
        index_update_inaccessible: true,
        tabs: await p.locator("#organization-maintenance-tab").count(),
      }),
    );
    console.log("REPRODUCED_HIDDEN_RECOVERY");
  } else {
    assert(visible);
    await button.click();
    await p.locator("#organization-maintenance-tab").waitFor();
    assert.equal(
      await p
        .getByRole("combobox", { name: "整理模式", exact: true })
        .locator("option")
        .allTextContents()
        .then((v) => v.join(",")),
      "快速整理,全量补充,深度重整",
    );
    assert.equal(await p.getByText(/归类状态读取失败/).count(), 0);
    await p.screenshot({ path: T + "/after-recovered.png", fullPage: true });
    await p.unroute("**/admin/organization?*");
    await p.route("**/admin/organization?*", (r) =>
      r.fulfill({ status: 409, json: { error: "layout_rebind_required" } }),
    );
    await p.reload();
    await p
      .getByRole("button", { name: "打开知识库核对位置", exact: true })
      .click();
    await p.getByText("批量核对外部改动", { exact: true }).click();
    await p
      .getByRole("button", { name: "扫描外部改动", exact: true })
      .waitFor();
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      T + "/browser.json",
      JSON.stringify({
        state: "PASS",
        checks: [
          "stale-index-message",
          "recovery-outside-hidden-tabs",
          "actual-synthetic-index-update",
          "three-modes-restored",
        ],
        errors,
      }),
    );
    assert.deepEqual(fs.readFileSync(note), noteBytes);
    console.log("RECOVERY_PASS");
  }
  await b.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
