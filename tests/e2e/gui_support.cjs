// Opt-in browser helpers for the isolated acceptance fixture, never the formal port.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const base = process.env.FIBER_GUI_ACCEPTANCE_URL || "http://127.0.0.1:18787";
const url = new URL(base);
assert.equal(url.hostname, "127.0.0.1");
assert(Number(url.port) >= 18000 && Number(url.port) <= 19000);
assert(
  process.env.FIBER_GUI_EVIDENCE,
  "Set a new FIBER_GUI_EVIDENCE output directory",
);
const output = process.env.FIBER_GUI_EVIDENCE;
fs.mkdirSync(output, { recursive: true });
const entries = [
  ["vaults", "知识库"],
  ["clients", "Agent 与客户端"],
  ["pending", "待审批"],
  ["batches", "待确认批次"],
  ["documents", "文档审核"],
  ["media", "媒体审核"],
  ["models", "模型与查询"],
  ["inventory", "资料与版本"],
  ["channels", "消息入口"],
  ["graph", "知识关联"],
  ["creation", "文档制作"],
  ["execution", "执行与费用"],
  ["backend", "后台与提醒"],
  ["backups", "备份与恢复"],
];
const viewports = [
  { id: "desktop", width: 1180, height: 820 },
  { id: "large", width: 1440, height: 900 },
  { id: "minimum", width: 760, height: 560 },
  { id: "mobile", width: 390, height: 844 },
  { id: "reflow", width: 320, height: 844 },
];
function write(name, value) {
  fs.writeFileSync(
    path.join(output, name + ".json"),
    JSON.stringify(value, null, 2) + "\n",
  );
}
function observe(page, result) {
  page.on("pageerror", (error) => result.page_errors.push(error.message));
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type()))
      result.console.push({ type: message.type(), text: message.text() });
  });
  page.on("response", (response) => {
    const request = response.request();
    result.requests.push({
      method: request.method(),
      path: new URL(request.url()).pathname,
      status: response.status(),
    });
  });
}
async function settle(page) {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForFunction(
    () =>
      !document.querySelector(
        '#workspace > section:not([hidden])[aria-busy="true"]',
      ),
  );
  await page.locator("#page-loading").waitFor({ state: "hidden" });
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)),
    );
  });
}
async function login(page) {
  await page.goto(base);
  await page.locator("#login-panel").waitFor({ state: "visible" });
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator('#login-form button[type="submit"]').click();
  await page.locator("#workspace").waitFor({ state: "visible" });
  await page.locator("#vault-list .row").first().waitFor({ state: "visible" });
  await settle(page);
}
async function navigate(page, id) {
  await page.waitForFunction(() =>
    window.matchMedia("(max-width: 767px)").matches
      ? document.body.dataset.sidebar === "drawer"
      : document.body.dataset.sidebar !== "drawer",
  );
  const button = page.locator(`[data-tab="${id}"]`);
  if (
    (await page.evaluate(() => document.body.dataset.sidebar === "drawer")) &&
    (await page.locator("#sidebar-toggle").getAttribute("aria-expanded")) !==
      "true"
  )
    await page.locator("#sidebar-toggle").click();
  await button.click();
  await settle(page);
  assert.equal(await button.getAttribute("aria-current"), "page");
  await page.evaluate(() => window.scrollTo(0, 0));
}
async function choose(page, name) {
  const picker = page.locator(".agent-picker");
  if (!(await picker.evaluate((node) => node.open)))
    await picker.locator("summary").click();
  await page.getByRole("button", { name, exact: true }).click();
  await page.locator("#agent-config-form").waitFor({ state: "visible" });
}
async function screenshot(page, result, name, fullPage = false) {
  await page.evaluate(() => document.fonts.ready);
  const file = name + ".png";
  await page.screenshot({
    path: path.join(output, file),
    fullPage,
    animations: "disabled",
  });
  const metrics = await page.evaluate(() => ({
    title: document.title,
    width: innerWidth,
    height: innerHeight,
    scroll_width: document.documentElement.scrollWidth,
    heading: document.querySelector("#page-name")?.textContent,
    meaningful_text_chars: document.body.innerText.length,
    framework_overlay: Boolean(
      document.querySelector(
        "vite-error-overlay,nextjs-portal,#webpack-dev-server-client-overlay",
      ),
    ),
    header_height: document
      .querySelector("body > header")
      ?.getBoundingClientRect().height,
    measured_toolbar_height: getComputedStyle(document.body).getPropertyValue(
      "--toolbar-height",
    ),
    dpr: devicePixelRatio,
  }));
  assert(metrics.meaningful_text_chars > 30);
  assert.equal(metrics.framework_overlay, false);
  result.screenshots.push({
    file,
    full_page: fullPage,
    sha256: crypto
      .createHash("sha256")
      .update(fs.readFileSync(path.join(output, file)))
      .digest("hex"),
    viewport: page.viewportSize(),
    ...metrics,
  });
  return metrics;
}
module.exports = {
  assert,
  fs,
  path,
  chromium,
  base,
  output,
  entries,
  viewports,
  write,
  observe,
  settle,
  login,
  navigate,
  choose,
  screenshot,
};
