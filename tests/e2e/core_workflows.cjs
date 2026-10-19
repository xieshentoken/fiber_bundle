// G2 rendered core flows against production APIs; extraction/graph worker inputs are replayed fixtures.
const {
  assert,
  fs,
  path,
  chromium,
  base,
  write,
  observe,
  login,
  navigate,
  settle,
  screenshot,
  viewports,
} = require("./gui_support.cjs");
const fixture = process.env.FIBER_GUI_FIXTURE;
assert(fixture && path.resolve(fixture).includes("/dist/gui-acceptance/"));
const manifest = JSON.parse(
  fs.readFileSync(path.join(fixture, "core-workflow-manifest.json")),
);
const result = {
  stage: "G2 core workflows",
  state: "RUNNING",
  synthetic: true,
  real_model: false,
  worker_outputs: manifest.worker_outputs,
  page_errors: [],
  console: [],
  requests: [],
  screenshots: [],
  checks: [],
  performance: [],
};
let browser, page;
function pass(name, evidence = {}) {
  result.checks.push({ name, state: "PASS", ...evidence });
  write("core", result);
  console.log("PASS " + name);
}
async function read(route) {
  const response = await page.request.get(base + route);
  assert(response.ok());
  return response.json();
}
async function ready(id) {
  await page.waitForFunction(
    (id) => !document.querySelector("#" + id)?.hasAttribute("aria-busy"),
    id,
  );
}
async function move(id) {
  const start = performance.now();
  await navigate(page, id);
  result.performance.push({
    entry: id,
    elapsed_ms: Math.round(performance.now() - start),
    includes_data_loading: true,
  });
}
async function capture(name) {
  for (const viewport of viewports) {
    await page.setViewportSize({
      width: viewport.width,
      height: viewport.height,
    });
    await settle(page);
    const metrics = await screenshot(
      page,
      result,
      name + "-" + viewport.id,
      true,
    );
    assert(
      metrics.scroll_width <= metrics.width,
      "populated " + name + " overflow at " + viewport.id,
    );
  }
  await page.setViewportSize({ width: 1180, height: 820 });
  await settle(page);
}
(async () => {
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 1180, height: 820 },
      locale: "zh-CN",
      reducedMotion: "reduce",
    });
    page = await context.newPage();
    observe(page, result);
    let accept = true;
    page.on("dialog", (d) => (accept ? d.accept() : d.dismiss()));
    let first = performance.now();
    await login(page);
    result.performance.push({
      entry: "signed-in first content",
      elapsed_ms: Math.round(performance.now() - first),
    });
    const personal = page.locator("#vault-list > .row").filter({
      has: page.getByRole("heading", { name: "personal", exact: true }),
    });
    await personal
      .getByRole("checkbox", { name: "默认参与查询", exact: true })
      .check();
    await page.waitForFunction(
      () =>
        Array.from(document.querySelectorAll("#vault-list .row"))
          .find((n) => n.querySelector("h3")?.textContent === "personal")
          ?.querySelector("input")?.disabled === false,
    );
    const attached = path.join(fixture, "gui-attached");
    fs.mkdirSync(attached);
    const original = Buffer.from("# G2 接入合成笔记\n记录压力2.4 MPa。\n");
    fs.writeFileSync(path.join(attached, "note.md"), original);
    await page.locator("#vault-admission-panel > summary").click();
    await page.locator("#vault-name").fill("G2 临时接入库");
    await page.locator("#vault-source").fill(attached);
    await page.locator("#vault-client").selectOption(manifest.client_id);
    await page.locator("#vault-preflight").click();
    await page.locator("#vault-preview h3").waitFor();
    assert(await page.locator("#vault-confirm").isEnabled());
    await capture("vault-preflight");
    await page.locator("#vault-confirm").click();
    await ready("vault-confirm");
    await page.waitForFunction(
      () => document.querySelector("#vault-jobs")?.textContent.includes("完成"),
      null,
      { timeout: 45000 },
    );
    await page.locator("#refresh").click();
    await ready("refresh");
    await settle(page);
    const vaultCard = page.locator("#vault-list > .row").filter({
      has: page.getByRole("heading", { name: "G2 临时接入库", exact: true }),
    });
    await vaultCard.waitFor();
    assert((await vaultCard.innerText()).includes("只读接入"));
    await vaultCard.locator(".vault-actions > summary").click();
    await vaultCard
      .getByRole("button", { name: "解除接入", exact: true })
      .click();
    await vaultCard.getByRole("button", { name: "取消", exact: true }).click();
    assert(
      await vaultCard
        .getByRole("button", { name: "解除接入", exact: true })
        .isVisible(),
    );
    await vaultCard
      .getByRole("button", { name: "解除接入", exact: true })
      .click();
    await vaultCard
      .getByRole("button", { name: "确认解除接入（保留原文件）", exact: true })
      .click();
    await page.waitForFunction(() =>
      document.querySelector("#vault-list")?.textContent.includes("已解除接入"),
    );
    assert.deepEqual(fs.readFileSync(path.join(attached, "note.md")), original);
    await page.locator("#vault-admission-panel > summary").click();
    assert.equal(
      await page.locator("#vault-admission-panel").evaluate((n) => n.open),
      false,
    );
    await vaultCard
      .getByRole("button", { name: "重新接入并重建索引", exact: true })
      .click();
    assert.equal(
      await page.locator("#vault-admission-panel").evaluate((n) => n.open),
      true,
    );
    assert.equal(await page.locator("#vault-source").inputValue(), attached);
    await capture("vault-reattach-prefill");
    pass(
      "vault preflight / actual attach and index / cancel unlink confirmation / unlink preserves original",
    );
    await move("inventory");
    await page.getByRole("button", { name: "导入资料", exact: true }).click();
    await page.locator("#import-client").selectOption(manifest.client_id);
    await page.locator("#import-vault").selectOption(manifest.vault_id);
    await page.locator("#import-action").selectOption("archive");
    await page.locator("#import-files").setInputFiles({
      name: "G2-local.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("G2 独立导入原件；压力 2.4 MPa。\n"),
    });
    await page.locator("#import-submit").click();
    await page.waitForFunction(
      () =>
        document
          .querySelector("#import-list")
          ?.textContent.includes("G2-local.txt") &&
        document
          .querySelector("#import-list")
          ?.textContent.includes("原件已保存"),
      null,
      { timeout: 30000 },
    );
    await capture("import-original-archived");
    const imported = await read(
      "/admin/imports?client_id=" +
        manifest.client_id +
        "&vault_id=" +
        manifest.vault_id,
    );
    assert(
      imported.some(
        (r) => r.filename === "G2-local.txt" && r.state === "archived",
      ),
    );
    pass(
      "local file / digest / actual resumable upload / archive / no automatic processing",
    );
    await move("documents");
    await page
      .getByRole("button", { name: "核对来源", exact: true })
      .first()
      .click();
    await page.locator(".document-review").waitFor();
    const review = page.locator(".document-review");
    assert(
      await review
        .getByRole("button", { name: "发布已审核文档", exact: true })
        .isDisabled(),
    );
    await review
      .locator('select[aria-label="来源单元 1 判断"]')
      .selectOption("accept");
    await review
      .getByLabel("来源单元 1 理由", { exact: true })
      .fill("合成夹具原文逐字核对，UI验收，不声明真实资料质量。");
    await review
      .getByText("我已核对原件、来源顺序及完整性", { exact: true })
      .locator("input")
      .check();
    await review
      .getByText("我已核对图像、数字、单位、表格与公式", { exact: true })
      .locator("input")
      .check();
    await review
      .getByRole("button", { name: "记录人工审核", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        !document.querySelector('.document-review button[type="button"]')
          .disabled,
    );
    await capture("document-review-gated");
    const downloaded = await page.request.get(
      base + (await review.locator("a[download]").getAttribute("href")),
    );
    assert.equal(downloaded.status(), 200);
    assert((await downloaded.text()).includes("青松合成资料"));
    await review
      .getByRole("button", { name: "发布已审核文档", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        document
          .querySelector(".document-review")
          ?.textContent.includes("已发布"),
      null,
      { timeout: 30000 },
    );
    pass(
      "replayed literal extraction / actual source download / review required / record judgment / explicit publication",
    );
    await move("batches");
    const batch = page.locator(`[data-batch-id="${manifest.batch_id}"]`);
    await batch.waitFor();
    await batch
      .getByRole("button", { name: "仅用于咨询", exact: true })
      .click();
    await batch.locator(".batch-question").waitFor();
    await batch
      .locator(".batch-question input")
      .fill("G2 合成批次中的测试压力是多少？");
    let releaseQuestion, enteredQuestion;
    const heldQuestion = new Promise((r) => (releaseQuestion = r)),
      receivedQuestion = new Promise((r) => (enteredQuestion = r));
    await page.route("**/admin/batches/*/choice", async (route) => {
      const response = await route.fetch();
      enteredQuestion();
      await heldQuestion;
      await route.fulfill({ response });
    });
    await batch.getByRole("button", { name: "补充问题", exact: true }).click();
    await receivedQuestion;
    await batch
      .locator(".batch-question input")
      .fill("等待保存时继续编辑的第二个问题");
    releaseQuestion();
    await page.waitForFunction(
      () =>
        document.querySelector(".batch-question")?.dataset.questionFixed ===
        "true",
    );
    await page.unroute("**/admin/batches/*/choice");
    assert.equal(
      await batch.locator(".batch-question input").inputValue(),
      "等待保存时继续编辑的第二个问题",
    );
    await capture("batch-question-selected");
    await batch.getByRole("button", { name: "取消批次", exact: true }).click();
    await page.waitForFunction(
      (id) => !document.querySelector(`[data-batch-id="${id}"]`),
      manifest.batch_id,
    );
    pass(
      "synthetic frozen Feishu event / actual consult selection / question / cancellation / no phone delivery",
    );
    await move("graph");
    await page.locator("#graph-client").selectOption(manifest.client_id);
    await settle(page);
    await page.locator("#graph-vault").selectOption(manifest.vault_id);
    await page.locator("#graph-refresh").click();
    await ready("graph-refresh");
    await page
      .locator("#graph-panel details")
      .filter({ has: page.locator("#graph-notes") })
      .locator("summary")
      .click();
    await page.getByRole("button", { name: "全选", exact: true }).click();
    assert(await page.locator("#graph-submit").isEnabled());
    await page.getByRole("button", { name: "反选", exact: true }).click();
    assert(await page.locator("#graph-submit").isDisabled());
    await page.getByRole("button", { name: "取消全选", exact: true }).click();
    await page.locator("#graph-filter").selectOption("moc");
    const candidate = page
      .locator("#graph-content [data-candidate-id]")
      .first();
    await candidate.waitFor();
    await candidate
      .getByRole("button", { name: "准备 MOC 写回", exact: true })
      .click();
    assert((await page.locator("#notice").innerText()).includes("先接受"));
    await candidate
      .getByLabel("审核理由")
      .fill("逐项核对合成来源，接受关联线索供UI验收。");
    await candidate
      .getByRole("button", { name: "接受线索", exact: true })
      .click();
    await candidate.getByText("已接受线索", { exact: true }).waitFor();
    await candidate
      .getByRole("button", { name: "准备 MOC 写回", exact: true })
      .click();
    const moc = page.locator("#graph-moc-form");
    await moc
      .getByRole("button", { name: "生成写回预览", exact: true })
      .click();
    await page.waitForFunction(() =>
      document
        .querySelector("#graph-moc-form pre")
        ?.textContent.includes("gui-graph-"),
    );
    assert(
      !fs.existsSync(path.join(fixture, "test vault/temp_vault1/graph-moc.md")),
    );
    await capture("moc-preview-not-written");
    await moc
      .getByRole("button", { name: "确认写入 MOC", exact: true })
      .click();
    await page.waitForFunction(() =>
      document
        .querySelector("#graph-writebacks")
        ?.textContent.includes("completed"),
    );
    assert(
      fs
        .readFileSync(
          path.join(fixture, "test vault/temp_vault1/graph-moc.md"),
          "utf8",
        )
        .includes("gui-graph-"),
    );
    pass(
      "graph note bulk selection / replayed candidates / reject premature MOC / actual review / preview / explicit MOC write",
    );
    await move("models");
    const details = page.locator("#models-panel > details");
    await details.locator("summary").click();
    const local = page
      .locator("#query-policy-fields fieldset")
      .filter({ has: page.getByText("本地查询", { exact: true }) });
    const output = local
      .getByText("输出 token 上限", { exact: true })
      .locator("input");
    await output.fill("1000");
    await page.locator('#query-policy-form button[type="submit"]').click();
    await page.waitForFunction(
      () =>
        document.querySelector("#query-policy-status")?.textContent ===
        "配置已保存；仅逐任务明确启动。",
    );
    assert(!fs.existsSync(path.join(fixture, "provider-calls.json")));
    await page.locator("#query-client").selectOption(manifest.client_id);
    await page
      .locator("#query-question")
      .fill("玄武温控器的测试设定温度是多少？");
    await page.locator("#query-search").fill("玄武温控器");
    await page.locator('#query-submit button[type="submit"]').click();
    await page.locator("#query-task-list .row").first().waitFor();
    assert(
      (await page.locator("#query-task-list").innerText()).includes("启动一次"),
    );
    pass(
      "model budget save / query queue is separate from model execution / no inference",
    );
    await move("creation");
    await page
      .locator("#creation-form select")
      .first()
      .selectOption(manifest.client_id);
    await page
      .locator("#creation-form textarea")
      .fill("基于固定合成来源制作短文档，保留引文，不启动模型。");
    await page
      .getByRole("button", { name: "准备文档任务", exact: true })
      .click();
    await page.locator("#creation-content .row").first().waitFor();
    await page
      .getByRole("button", { name: "查看版本、输入与交付状态", exact: true })
      .first()
      .click();
    await page.locator(".creation-details").waitFor();
    await page
      .getByText("补充要求", { exact: true })
      .locator("textarea")
      .fill("增加一段使用范围说明。");
    await page
      .getByRole("button", { name: "确认补充到下一版本", exact: true })
      .click();
    await page.waitForFunction(() =>
      document
        .querySelector("#creation-content")
        ?.textContent.includes("要求修订 2"),
    );
    await page
      .getByRole("button", { name: "查看版本、输入与交付状态", exact: true })
      .first()
      .click();
    await page
      .getByRole("button", { name: "取消文档及后续版本", exact: true })
      .click();
    await page.waitForFunction(() =>
      document
        .querySelector("#creation-content")
        ?.textContent.includes("cancelled"),
    );
    pass(
      "fixed query input / document preparation / amendment version / cancel without execution or delivery",
    );
    await move("backups");
    await page.locator("#backups-prepare").click();
    await page.locator("#backups-content .record").waitFor();
    assert(
      (await page.locator("#backups-content").innerText()).includes(
        "等待离线执行",
      ),
    );
    assert(!fs.existsSync(path.join(fixture, "backups")));
    await capture("backup-prepared-offline");
    pass(
      "GUI backup intent / terminal-only password and execution / no automatic encryption or restore",
    );
    // A network outage is injected only into browser reads; the formal/fixture processes stay up.
    await page.route("**/health", (route) => route.abort("connectionrefused"));
    await page.locator("#refresh").click();
    await ready("refresh");
    await page.locator("#notice.error").waitFor();
    assert((await page.locator("#notice").innerText()).includes("无法连接"));
    assert(await page.locator("#workspace").isVisible());
    await screenshot(page, result, "network-failure-keeps-session");
    await page.unroute("**/health");
    await page.locator("#refresh").click();
    await ready("refresh");
    assert.equal(
      await page.locator("#connection").getAttribute("data-status"),
      "connected",
    );
    pass(
      "read network outage / visible recovery / session retained / explicit reconnect",
    );
    await page.route("**/admin/session", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: '{"error":"session_expired"}',
      }),
    );
    await page.locator("#refresh").click();
    await ready("refresh");
    await page.locator("#login-panel").waitFor();
    assert(await page.locator("#workspace").isHidden());
    assert.equal(await page.locator("#password").inputValue(), "");
    await page.unroute("**/admin/session");
    pass("expired session / clear workspace / credentials not repopulated");
    assert(!fs.existsSync(path.join(fixture, "provider-calls.json")));
    assert.equal(result.page_errors.length, 0);
    result.expected_http_errors = [401];
    result.state = "PASS";
    write("core", result);
    console.log(
      JSON.stringify({
        state: result.state,
        checks: result.checks.length,
        real_model: false,
      }),
    );
  } catch (error) {
    result.state = "FAIL";
    result.failure = error.message;
    if (page) await screenshot(page, result, "core-failure").catch(() => {});
    write("core", result);
    console.error(error.stack);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
})();
