// Opt-in G2: production management API, installed Hermes/MCP, synthetic inference.
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
  choose,
  screenshot,
} = require("./gui_support.cjs");
const fixture = process.env.FIBER_GUI_FIXTURE;
assert(fixture && path.resolve(fixture).includes("/dist/gui-acceptance/"));
const result = {
  stage: "G2 Agent lifecycle",
  state: "RUNNING",
  real_model: false,
  page_errors: [],
  console: [],
  requests: [],
  screenshots: [],
  checks: [],
  dialogs: [],
};
let browser, noteOriginal, note;
function pass(name, evidence = {}) {
  result.checks.push({ name, state: "PASS", ...evidence });
  write("lifecycle", result);
  console.log("PASS " + name);
}
async function api(page, route, method = "GET", body) {
  return page.evaluate(
    async ({ route, method, body }) => {
      const session = await (await fetch("/admin/session")).json();
      const response = await fetch(route, {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": session.csrf,
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      return { status: response.status, data: await response.json() };
    },
    { route, method, body },
  );
}
async function done(page, kind, previous) {
  await page.waitForFunction(
    ({ kind, previous }) => {
      const n = document.querySelector("#agent-result");
      return (
        n?.dataset.kind === kind &&
        n.dataset.operationId !== previous &&
        ["succeeded", "failed", "cancelled", "interrupted"].includes(
          n.dataset.state,
        )
      );
    },
    { kind, previous },
    { timeout: 50000 },
  );
  return page.locator("#agent-result").getAttribute("data-state");
}
async function operation(page, button, kind) {
  const old = await page
    .locator("#agent-result")
    .getAttribute("data-operation-id");
  await page.getByRole("button", { name: button, exact: true }).click();
  assert.equal(await done(page, kind, old), "succeeded");
}
function providerCalls() {
  const file = path.join(fixture, "provider-calls.json");
  return fs.existsSync(file)
    ? JSON.parse(fs.readFileSync(file)).calls.length
    : 0;
}
async function query(page, question, { delay = false } = {}) {
  const old = await page
      .locator("#agent-result")
      .getAttribute("data-operation-id"),
    count = providerCalls();
  await page.locator("#agent-question").fill(question);
  await page.locator("#agent-test-queries").fill("玄武温控器");
  await page
    .getByRole("button", { name: "执行 Hermes 问答测试", exact: true })
    .click();
  if (delay) {
    const deadline = Date.now() + 20000;
    while (providerCalls() === count && Date.now() < deadline)
      await page.waitForTimeout(100);
    assert.equal(providerCalls(), count + 1, "provider call was not reached");
  }
  return old;
}
(async () => {
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
        viewport: { width: 1180, height: 820 },
        locale: "zh-CN",
      }),
      page = await context.newPage();
    observe(page, result);
    let accept = true;
    page.on("dialog", async (d) => {
      result.dialogs.push({ message: d.message(), accepted: accept });
      await (accept ? d.accept() : d.dismiss());
    });
    await login(page);
    await navigate(page, "clients");
    await page.locator("#agent-name").fill("G2 Hermes · 合成服务");
    await page.locator('[name="vault_ids"]').first().check();
    await page.locator("#agent-model").selectOption("opencodego-primary");
    assert(
      (await page.locator("#agent-config-form").innerText()).includes(
        "发送到 OpenCodeGo 云端",
      ),
    );
    await screenshot(page, result, "explicit-cloud-scope");
    await page.locator("#agent-model").selectOption("local-primary");
    assert(
      !(await page.locator("#agent-config-form").innerText()).includes(
        "发送到 OpenCodeGo 云端",
      ),
    );
    await page.locator('#agent-config-form button[type="submit"]').click();
    await page.locator("#agent-apply").waitFor({ state: "visible" });
    let rows = (await api(page, "/admin/agent-connections")).data;
    let row = rows.find((r) => r.configuration.name === "G2 Hermes · 合成服务");
    assert(row);
    const id = row.id;
    assert.equal(row.access_state, "paused");
    assert.equal(providerCalls(), 0);
    await operation(page, "应用接入配置", "apply_config");
    await operation(page, "授权并启用查询", "probe");
    await page.waitForFunction(() =>
      document
        .querySelector("#agent-detail-states")
        ?.textContent.includes("MCP 工具可用"),
    );
    assert(
      (await page.locator("#agent-detail-states").innerText()).includes(
        "MCP 工具可用",
      ),
    );
    assert.equal(providerCalls(), 0);
    pass(
      "create / explicit cloud scope / select synthetic local / apply / authorize / actual stdio probe",
      { connection: id },
    );
    const old = await query(
      page,
      "合成测试冷却器在 2 L/min 条件下的设定温度是多少？",
    );
    assert.equal(await done(page, "test_query", old), "succeeded");
    const text = await page.locator("#agent-result").innerText();
    for (const part of [
      "回答草稿",
      "37.4",
      "[1]",
      "非知识库证据",
      "生成调用 1 次",
      "用量已报告",
    ])
      assert(text.includes(part), part);
    assert.equal(providerCalls(), 1);
    await page
      .locator("#agent-result details")
      .first()
      .locator("summary")
      .click();
    await screenshot(page, result, "hermes-draft-citation", true);
    pass(
      "retrieve before answer / draft / claim citation / supplement separated / source / reported usage",
    );
    const requestsBefore = result.requests.filter(
      (x) => x.method === "POST" && x.path.endsWith("/operations"),
    ).length;
    const delayed = await query(page, "G2延迟：合成测试冷却器的温度是多少？", {
      delay: true,
    });
    assert(
      await page.locator('.agent-test button[type="submit"]').isDisabled(),
    );
    // Programmatic duplicate submit tests the event handler as well as the disabled button.
    await page.locator(".agent-test").evaluate((n) => {
      n.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      n.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await page.waitForTimeout(200);
    assert.equal(providerCalls(), 2);
    await page.locator("#agent-cancel").focus();
    await page.waitForTimeout(1000);
    assert.equal(
      await page.evaluate(() => document.activeElement.id),
      "agent-cancel",
    );
    assert.equal(
      result.requests.filter(
        (x) => x.method === "POST" && x.path.endsWith("/operations"),
      ).length,
      requestsBefore + 1,
    );
    await page
      .getByRole("button", { name: "取消受管操作", exact: true })
      .click();
    assert.equal(await done(page, "test_query", delayed), "cancelled");
    assert.equal(
      await page.evaluate(() => document.activeElement.id),
      "agent-result",
    );
    assert(
      !(await page.locator("#agent-result").innerText()).includes("回答草稿"),
    );
    assert(
      (await page.locator("#agent-question").inputValue()).includes("G2延迟"),
    );
    await screenshot(page, result, "cancelled-question-retained");
    pass(
      "queued/running gate duplicate submit / cancel owned operation / retain question / no answer",
    );
    const failed = await query(page, "G2失败：合成测试冷却器的温度是多少？");
    assert.equal(await done(page, "test_query", failed), "failed");
    const failure = await page.locator("#agent-result").innerText();
    assert(failure.includes("不会自动重试"));
    assert(failure.includes("agent_provider_"));
    assert(
      (await page.locator("#agent-question").inputValue()).includes("G2失败"),
    );
    await page.waitForTimeout(1000);
    assert.equal(providerCalls(), 3);
    await screenshot(page, result, "provider-failure-recovery");
    pass(
      "provider failure / understandable manual recovery / diagnostic / no retry / question retained",
    );
    // The path is restricted to this acceptance fixture. Preserve and restore source bytes.
    note = path.join(fixture, "test vault/temp_vault1/cooler.md");
    noteOriginal = fs.readFileSync(note);
    const stale = await query(page, "G2延迟：合成测试冷却器的当前温度？", {
      delay: true,
    });
    fs.writeFileSync(
      note,
      Buffer.concat([
        noteOriginal,
        Buffer.from("\n合成来源在执行期间变化。\n"),
      ]),
    );
    assert.equal(await done(page, "test_query", stale), "failed");
    assert(
      !(await page.locator("#agent-result").innerText()).includes("回答草稿"),
    );
    assert(
      (await page.locator("#agent-result").innerText()).includes("agent_"),
    );
    fs.writeFileSync(note, noteOriginal);
    pass(
      "actual source bytes changed during generation / stale answer withheld",
    );
    const paused = await query(page, "G2延迟：合成测试冷却器的温度？", {
      delay: true,
    });
    await page.getByRole("button", { name: "暂停查询", exact: true }).click();
    await done(page, "test_query", paused);
    assert(
      !(await page.locator("#agent-result").innerText()).includes("回答草稿"),
    );
    assert(
      await page.locator('.agent-test button[type="submit"]').isDisabled(),
    );
    assert(
      (await page.locator("#agent-question").inputValue()).includes("G2延迟"),
    );
    row = (await api(page, "/admin/agent-connections/" + id)).data;
    const denied = await api(
      page,
      "/admin/agent-connections/" + id + "/operations",
      "POST",
      {
        kind: "test_query",
        request_id: crypto.randomUUID(),
        expected_revision: row.revision,
        request: {
          request_id: crypto.randomUUID(),
          question: "合成问题",
          retrieval_queries: ["合成测试冷却器"],
        },
      },
    );
    assert.equal(denied.status, 409);
    assert.equal(denied.data.error, "agent_paused");
    pass(
      "pause during generation / hide answer / retain question / server rejects bypassed UI",
    );
    await page.locator("#agent-question").fill("尚未提交的新问题");
    accept = false;
    await page.locator('[data-tab="vaults"]').click();
    assert(await page.locator("#clients-panel").isVisible());
    assert.equal(
      await page.locator("#agent-question").inputValue(),
      "尚未提交的新问题",
    );
    accept = true;
    pass("leave guard dismiss keeps page and draft");
    // Fetch the real PATCH response, delaying only delivery while the user continues editing.
    let release, entered;
    const held = new Promise((r) => (release = r)),
      started = new Promise((r) => (entered = r));
    await page.route("**/admin/agent-connections/*", async (route) => {
      if (route.request().method() !== "PATCH") return route.continue();
      const response = await route.fetch();
      entered();
      await held;
      await route.fulfill({ response });
    });
    await page.locator("#agent-name").fill("G2 已提交名称");
    await page.locator('#agent-config-form button[type="submit"]').click();
    await started;
    assert(
      await page
        .getByRole("button", { name: "新建连接", exact: true })
        .isDisabled(),
    );
    await page.locator("#agent-name").fill("G2 响应等待期间的新编辑");
    await page.locator("#agent-question").fill("保存等待期间的新问题");
    release();
    await page
      .locator('#agent-config-form button[type="submit"]')
      .waitFor({ state: "visible" });
    await page.waitForFunction(
      () =>
        !document.querySelector('#agent-config-form button[type="submit"]')
          .disabled,
    );
    assert.equal(
      await page.locator("#agent-name").inputValue(),
      "G2 响应等待期间的新编辑",
    );
    assert.equal(
      await page.locator("#agent-question").inputValue(),
      "保存等待期间的新问题",
    );
    row = (await api(page, "/admin/agent-connections/" + id)).data;
    assert.equal(row.configuration.name, "G2 已提交名称");
    await page.unroute("**/admin/agent-connections/*");
    pass(
      "actual save / response-delivery delay / newer configuration and question edits retained",
    );
    await page.getByRole("button", { name: "刷新连接", exact: true }).click();
    await page.waitForFunction(
      () =>
        document
          .querySelector("#agent-connections")
          .getAttribute("aria-busy") === "false",
    );
    await page.locator("#agent-config-form").waitFor();
    const other = await browser.newContext({
        viewport: { width: 1180, height: 820 },
      }),
      second = await other.newPage();
    observe(second, result);
    await login(second);
    await navigate(second, "clients");
    await choose(second, "G2 已提交名称");
    await page.locator("#agent-name").fill("G2 窗口甲已保存");
    await page.locator('#agent-config-form button[type="submit"]').click();
    await page.waitForFunction(
      () =>
        document.querySelector(".agent-detail h3")?.textContent ===
        "G2 窗口甲已保存",
    );
    await second.locator("#agent-name").fill("G2 窗口乙未保存");
    await second.locator('#agent-config-form button[type="submit"]').click();
    await second.waitForFunction(() =>
      document
        .querySelector("#notice")
        ?.textContent.includes("agent_revision_conflict"),
    );
    assert.equal(
      await second.locator("#agent-name").inputValue(),
      "G2 窗口乙未保存",
    );
    assert.equal(
      (await api(page, "/admin/agent-connections/" + id)).data.configuration
        .name,
      "G2 窗口甲已保存",
    );
    await screenshot(second, result, "multi-window-conflict-draft");
    await other.close();
    pass("multi-window CAS conflict / preserve losing draft / no overwrite");
    await page.getByRole("button", { name: "新建连接", exact: true }).click();
    await page.locator("#agent-name").fill("G2 通用 MCP");
    await page.locator("#agent-type").selectOption("mcp");
    await page.locator('[name="vault_ids"]').first().check();
    assert(await page.locator("#agent-model").isDisabled());
    await page.locator('#agent-config-form button[type="submit"]').click();
    await page.locator("#agent-apply").waitFor();
    await operation(page, "应用接入配置", "apply_config");
    await operation(page, "授权并启用查询", "probe");
    await page
      .getByRole("button", { name: "导出 MCP 配置", exact: true })
      .click();
    const exported = await page.getByLabel("无秘密 MCP 配置").inputValue();
    assert(exported.includes("mcpServers"));
    assert(!/Bearer |sk-|token":/.test(exported));
    assert(
      (await page.locator(".agent-detail").innerText()).includes(
        "外部宿主仍需",
      ) ||
        (await page.locator(".agent-detail").innerText()).includes(
          "导出不代表宿主已加载",
        ),
    );
    const lifecycle = page
      .locator(".agent-detail details")
      .filter({ has: page.getByText("凭证与撤销", { exact: true }) });
    await lifecycle.locator("summary").click();
    await operation(page, "轮换凭证", "rotate_credential");
    await page.waitForFunction(() =>
      document
        .querySelector("#agent-detail-states")
        ?.textContent.includes("访问已暂停"),
    );
    assert(
      (await page.locator("#agent-detail-states").innerText()).includes(
        "访问已暂停",
      ),
    );
    await operation(page, "应用接入配置", "apply_config");
    await operation(page, "授权并启用查询", "probe");
    await page
      .getByRole("button", { name: "永久撤销连接", exact: true })
      .click();
    await page.waitForFunction(() =>
      document
        .querySelector(".agent-detail")
        ?.textContent.includes("连接已永久撤销"),
    );
    assert(await page.locator("#agent-name").isDisabled());
    assert.equal(providerCalls(), 5);
    await screenshot(page, result, "mcp-revoked");
    pass(
      "generic MCP / actual probe / secret-free export / rotate pauses / reapply / enable / permanent revoke",
    );
    assert.equal(result.page_errors.length, 0);
    result.provider_calls = providerCalls();
    result.expected_http_errors = [401, 409, 500];
    result.state = "PASS";
    write("lifecycle", result);
    console.log(
      JSON.stringify({
        state: result.state,
        checks: result.checks.length,
        provider_calls: providerCalls(),
        real_model: false,
      }),
    );
  } catch (error) {
    result.state = "FAIL";
    result.failure = error.message;
    write("lifecycle", result);
    console.error(error.stack);
    process.exitCode = 1;
  } finally {
    if (noteOriginal) fs.writeFileSync(note, noteOriginal);
    if (browser) await browser.close();
  }
})();
