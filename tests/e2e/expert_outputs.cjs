// Actual isolated management GUI plus actual compiled-package output; no production profile.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const base = process.env.FIBER_GUI_ACCEPTANCE_URL;
const output = process.env.FIBER_GUI_EVIDENCE;
assert.equal(new URL(base).hostname, "127.0.0.1");
assert(!["8765", "8766"].includes(new URL(base).port));
const packet = JSON.parse(
  fs.readFileSync(process.env.FIBER_MEDIA_PACKET_PATH, "utf8"),
);
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1180, height: 900 },
  });
  const errors = [],
    expectedConsole = [],
    expectedHTTP = [],
    requests = [];
  let phase = "auth_probe";
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      const url = message.location().url;
      if (
        phase === "auth_probe" &&
        message.text().includes("401 (Unauthorized)") &&
        url.endsWith("/admin/session")
      )
        expectedConsole.push({ text: message.text(), url, phase });
      else errors.push(message.text());
    }
  });
  page.on("request", (request) =>
    requests.push(new URL(request.url()).pathname),
  );
  page.on("response", (response) => {
    if (response.status() < 400) return;
    const url = response.url();
    if (
      phase === "auth_probe" &&
      response.status() === 401 &&
      url.endsWith("/admin/session")
    )
      expectedHTTP.push({ url, status: 401, phase });
    else errors.push(`HTTP ${response.status()} ${url}`);
  });
  try {
    await page.goto(base);
    assert.match(await page.title(), /Fiber bundle/);
    await page.locator("#password").fill("synthetic-password");
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator("#workspace").waitFor({ state: "visible" });
    phase = "expert_query";
    await page.locator('[data-tab="clients"]').click();
    const connection = page.locator(
      `.agent-choice[data-connection-id="${process.env.FIBER_MEDIA_CONNECTION_ID}"]`,
    );
    await connection.click();
    await page.getByText("知识模块与来源（只读）", { exact: true }).click();
    await page
      .getByRole("button", { name: "读取授权知识目录", exact: true })
      .click();
    const query = page.getByLabel("专家模块查询").first();
    await query.fill("Figure 1");
    await page
      .getByRole("button", { name: "查询已发布专家 Draft", exact: true })
      .first()
      .click();
    const result = page.locator(".agent-detail .expert-results").first();
    await result.locator("img").waitFor();
    await page.waitForFunction(() =>
      [...document.querySelectorAll(".expert-media img")].every(
        (i) => i.complete && i.naturalWidth,
      ),
    );
    assert.equal(await result.locator("img").count(), 1);
    assert.match(await result.innerText(), /证据 ID：ev-/);
    assert.match(await result.innerText(), /对象 ID：ko-/);
    assert.match(await result.innerText(), /聚合物网络示意图/);
    assert.match(await result.innerText(), /知识未核实/);
    await result.locator("summary").click();
    assert.match(await result.locator("details").innerText(), /physical_page/);
    await result.screenshot({
      path: path.join(output, "expert-query-desktop.png"),
    });
    const download = page.waitForEvent("download");
    await page
      .getByRole("button", {
        name: "下载专家回答 Markdown 与图片",
        exact: true,
      })
      .first()
      .click();
    await (await download).saveAs(path.join(output, "expert-answer.zip"));
    await query.fill("");
    await result.locator("summary").click();
    await page
      .locator(
        `.agent-choice[data-connection-id="${process.env.FIBER_MEDIA_OPERATION_CONNECTION_ID}"]`,
      )
      .click();
    await page.locator("#agent-question").fill("Figure 1");
    await page.locator("#agent-test-queries").fill("Figure");
    await page
      .getByRole("button", { name: "执行 Hermes 问答测试", exact: true })
      .click();
    await page
      .locator('#agent-result[data-state="succeeded"] .expert-results img')
      .waitFor({ timeout: 45000 });
    const mixedResult = page.locator("#agent-result");
    assert.match(await mixedResult.innerText(), /Ordinary/);
    assert.match(await mixedResult.innerText(), /知识未核实/);
    assert.equal(await mixedResult.locator("img").count(), 1);
    assert.equal(
      requests.filter((path) => path.endsWith("/knowledge-evidence")).length,
      1,
    );
    await mixedResult.screenshot({
      path: path.join(output, "mixed-operation-desktop.png"),
    });
    await mixedResult.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: path.join(output, "mixed-operation-page-desktop.png"),
    });
    // Only this failure injection is synthetic; the mixed Operation above used real APIs.
    await page.evaluate(
      async ({ packet }) => {
        const { expertResults } = await import("/static/expert_results.js");
        const panel = document.createElement("section");
        panel.id = "mixed-output-proof";
        document.querySelector(".agent-detail").append(panel);
        const partial = structuredClone(packet);
        partial.expert_blocks[0].media.push({
          resource_id: "vo-missing",
          caption: "缺图",
          status: "missing",
        });
        partial.expert_blocks[0].behavior = "abstain";
        const fragment = expertResults(partial);
        fragment.id = "partial-output-proof";
        panel.append(fragment);
      },
      { packet },
    );
    await page
      .locator('#partial-output-proof[data-presentation="partially_supported"]')
      .waitFor();
    assert.equal(await page.locator("#partial-output-proof img").count(), 1);
    assert.match(
      await page.locator("#partial-output-proof").innerText(),
      /图表资源缺失/,
    );
    assert.match(
      await page.locator("#partial-output-proof").innerText(),
      /保持弃答/,
    );
    await page
      .locator("#mixed-output-proof")
      .screenshot({ path: path.join(output, "mixed-and-partial-desktop.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(
      () => document.body.dataset.sidebar === "drawer",
    );
    const sidebar = page.locator("#sidebar-toggle");
    if ((await sidebar.getAttribute("aria-expanded")) === "true")
      await sidebar.click();
    await page.waitForFunction(
      () =>
        document.body.dataset.drawerOpen === "false" &&
        document.getElementById("sidebar").getBoundingClientRect().right <= 0 &&
        !document.querySelector("main").inert,
    );
    await page
      .locator("#partial-output-proof")
      .evaluate((node) => node.scrollIntoView({ block: "start" }));
    const box = await page.locator("#partial-output-proof").boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 391);
    const image = await page.locator("#partial-output-proof img").boundingBox();
    assert(image.x >= 0 && image.x + image.width <= 391);
    await page.screenshot({ path: path.join(output, "partial-mobile.png") });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
      true,
    );
    await page.setViewportSize({ width: 1180, height: 900 });
    await page.locator("#agent-question").fill("needleonly");
    await page.locator("#agent-test-queries").fill("needleonly");
    const previousOperation = await page
      .locator("#agent-result")
      .getAttribute("data-operation-id");
    await page
      .getByRole("button", { name: "执行 Hermes 问答测试", exact: true })
      .click();
    await page.waitForFunction(
      (previous) => {
        const result = document.querySelector("#agent-result");
        return (
          result.dataset.operationId !== previous &&
          result.dataset.state === "succeeded"
        );
      },
      previousOperation,
      { timeout: 45000 },
    );
    assert.equal(await page.locator("#agent-result img").count(), 0);
    assert.equal(
      await page.locator("#agent-result .expert-results").count(),
      0,
    );
    await page
      .locator("#agent-result details")
      .first()
      .locator("summary")
      .click();
    assert.match(
      await page.locator("#agent-result").innerText(),
      /!\[\[secret.png\]\]/,
    );
    assert.equal(
      requests.some((p) => /private\.png|secret\.png/.test(p)),
      false,
    );
    await page.locator("#agent-result").screenshot({
      path: path.join(output, "ordinary-operation-desktop.png"),
    });
    assert.deepEqual(errors, []);
    fs.writeFileSync(
      path.join(output, "browser-checks.json"),
      JSON.stringify(
        {
          status: "PASS",
          environment: base,
          title: await page.title(),
          viewports: ["1180x900", "390x844"],
          browser: "Playwright; Browser plugin not available",
          errors,
          expectedConsole,
          expectedHTTP,
          checks: [
            "actual expert GUI query",
            "source evidence IDs",
            "actual Markdown download",
            "actual mixed Operation UI and resource reads",
            "actual ordinary-only Operation UI",
            "per-item missing placeholder",
            "abstention retained",
            "ordinary image syntax stays text",
            "no private image requests",
          ],
        },
        null,
        2,
      ),
    );
  } catch (error) {
    fs.writeFileSync(
      path.join(output, "browser-failure.json"),
      JSON.stringify(
        {
          errors,
          body: await page.locator("body").innerText(),
          message: String(error),
        },
        null,
        2,
      ),
    );
    await page.screenshot({
      path: path.join(output, "failure.png"),
      fullPage: false,
    });
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
