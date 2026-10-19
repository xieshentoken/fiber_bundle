import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { parseArgs } from "node:util";
import assert from "node:assert/strict";
import { chromium, _electron } from "playwright";
import { checkPage } from "lintapple";
import { checkContracts, checkInteractions } from "./lintapple_guards.mjs";

// Same-page inspection only: never export credentials or copy an authenticated session.
const { values } = parseArgs({
  options: {
    url: { type: "string", default: "http://127.0.0.1:8766/" },
    electron: { type: "string" },
    profile: { type: "string" },
    output: { type: "string" },
    assets: { type: "string" },
    "require-auth": { type: "boolean", default: false },
  },
});
const url = new URL(values.url);
assert(
  ["127.0.0.1", "localhost"].includes(url.hostname),
  "Only loopback applications are supported",
);
assert.equal(
  Boolean(values.electron),
  Boolean(values.profile),
  "Electron requires its existing independent profile",
);
const out =
  values.output ||
  (await fs.mkdtemp(path.join(os.tmpdir(), "fiber-lintapple-")));
await fs.mkdir(out, { recursive: true, mode: 0o700 });
const policy = JSON.parse(
  await fs.readFile(new URL("../../ui-policy.json", import.meta.url)),
);
const report = {
  scope: "Same-page viewport emulation; reduced motion; no business writes",
  results: [],
  errors: [],
  writes: [],
};
const tabs = [
  "home",
  "vaults",
  "imports",
  "inventory",
  "graph",
  "worklist",
  "creation",
  "channels",
  "clients",
  "models",
  "execution",
  "backend",
  "backups",
  "settings",
];
let owner, page;
async function settle() {
  await page.waitForLoadState("networkidle");
  await page.waitForFunction(
    () => document.documentElement.dataset.fontReady === "true",
  );
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(
      document
        .getAnimations()
        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
        .map((a) => a.finished.catch(() => {})),
    );
  });
}
async function navigate(tab) {
  if (
    await page.evaluate(
      () =>
        document.body.dataset.sidebar === "drawer" &&
        document.body.dataset.drawerOpen !== "true",
    )
  )
    await page.locator("#sidebar-toggle").click();
  await page.locator(`.sidebar [data-tab="${tab}"]`).click();
  await page.waitForFunction(
    (key) =>
      document
        .querySelector(`.sidebar [data-tab="${key}"]`)
        ?.getAttribute("aria-current") === "page",
    tab,
  );
  await settle();
}
try {
  if (values.electron) {
    owner = await _electron.launch({
      executablePath: values.electron,
      args: ["--url", url.href, "--user-data", values.profile],
      timeout: 30000,
    });
    page = await owner.firstWindow();
  } else {
    owner = await chromium.launch();
    page = await owner.newPage();
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  page.on("pageerror", (error) => report.errors.push(error.message));
  page.on("request", (request) => {
    if (!["GET", "HEAD"].includes(request.method()))
      report.writes.push({
        method: request.method(),
        path: new URL(request.url()).pathname,
      });
  });
  if (values.assets) {
    const assets = JSON.parse(await fs.readFile(values.assets));
    for (const name of assets) {
      assert(
        /^[\w.-]+\.(html|css|js)$/.test(name),
        "Only direct UI files can be overridden",
      );
      await page.route(
        new URL(name === "index.html" ? "/" : "/static/" + name, url).href,
        (route) =>
          route.fulfill({
            path: path.resolve("src/fiber_bundle/web", name),
            contentType: name.endsWith(".css")
              ? "text/css"
              : name.endsWith(".js")
                ? "text/javascript"
                : "text/html",
          }),
      );
    }
  }
  await page.goto(url.href);
  await settle();
  const authenticated = await page.locator("#workspace").isVisible();
  assert(
    authenticated || !values["require-auth"],
    "Existing profile is not logged in; please log in normally",
  );
  for (const viewport of policy.viewports) {
    await page.setViewportSize(viewport);
    for (const tab of authenticated ? tabs : ["login"]) {
      if (authenticated) await navigate(tab);
      else await settle();
      await checkContracts(page);
      await checkInteractions(page);
      await settle();
      const result = await checkPage(page, policy);
      report.results.push({
        tab,
        viewport,
        theme: await page.locator("html").getAttribute("data-theme"),
        ...result,
      });
      await fs.writeFile(
        path.join(out, "report.json"),
        JSON.stringify(report, null, 2),
      );
      if (
        ["home", "imports", "models", "clients", "settings", "login"].includes(
          tab,
        )
      )
        await page.screenshot({
          path: path.join(out, `${viewport.width}-${tab}.png`),
        });
      console.log(
        JSON.stringify({
          viewport: viewport.width,
          tab,
          findings: result.findings.length,
          contracts: "PASS",
        }),
      );
    }
  }
  assert.deepEqual(report.errors, [], "Page errors");
  assert.deepEqual(report.writes, [], "Unexpected business requests");
  assert.equal(
    report.results.reduce((sum, r) => sum + r.findings.length, 0),
    0,
    "UI findings remain; see report",
  );
  report.state = "PASS";
} catch (error) {
  report.state = "FAIL";
  report.failure = error.stack;
  process.exitCode = 1;
  console.error(error.stack);
} finally {
  await fs.writeFile(
    path.join(out, "report.json"),
    JSON.stringify(report, null, 2),
  );
  if (page && values.electron) {
    await page.setViewportSize({ width: 1180, height: 788 }).catch(() => {});
    if (
      await page
        .locator("#workspace")
        .isVisible()
        .catch(() => false)
    )
      await navigate("home").catch(() => {});
  }
  await owner?.close();
  console.log("Evidence: " + out);
}
