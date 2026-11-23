// Isolated UI component regression: no production data, model calls, or apply controls.
const { chromium } = require(
  process.env.FIBER_PLAYWRIGHT_MODULE || "playwright",
);
const http = require("node:http"),
  fs = require("node:fs"),
  path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../../src/fiber_bundle/web");
const out = process.env.FIBER_GUI_EVIDENCE;
(async () => {
  const server = http.createServer((req, res) => {
    if (req.url === "/") {
      res.setHeader("Content-Type", "text/html");
      res.end(
        '<!doctype html><html><body><div id="notice"></div><div id="fixture"></div></body></html>',
      );
      return;
    }
    const name = path.resolve(root, "." + req.url.split("?")[0]);
    if (!name.startsWith(root + path.sep) || !fs.existsSync(name)) {
      res.writeHead(404);
      res.end();
      return;
    }
    res.setHeader(
      "Content-Type",
      name.endsWith(".js") ? "text/javascript" : "text/css",
    );
    res.end(fs.readFileSync(name));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  let browser;
  try {
    browser = await chromium.launch();
    const page = await browser.newPage({
        viewport: { width: 1180, height: 900 },
      }),
      errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    const result = await page.evaluate(async () => {
      const { maintenanceSection } =
        await import("/organization_maintenance.js");
      const { retainView } = await import("/view_sections.js");
      const { setLocale } = await import("/i18n/index.js");
      const { createOrganizationView } = await import("/organization.js");
      const root = document.getElementById("fixture");
      const coverage = {
        complete: false,
        unavailable_count: 1,
        unavailable: [
          { path: "Inbox/edited.md", reason: "vault_sync_cloud_pending" },
        ],
      };
      const job = {
        id: "synthetic",
        maintenance: true,
        state: "preview",
        stage: "planning",
        mode: "quick",
        created_at: 100,
        items: [],
        coverage,
        application_blocked: "organization_archive_scope_incomplete",
        new_mocs: [],
      };
      const draft = { mode: "quick", title: "", topic: "" };
      const render = () =>
        retainView(root, () =>
          root.replaceChildren(
            maintenanceSection(
              { jobs: [job], mocs: [] },
              {},
              {},
              async () => {},
              draft,
            ),
          ),
        );
      render();
      let prepare = root.querySelector(
        '[data-view-key="maintenance-preparation"]',
      );
      prepare.open = true;
      root.querySelector('[data-view-key="maintenance-new-moc"]').open = true;
      const mode = root.querySelector('select[aria-label="整理模式"]');
      mode.value = "deep";
      mode.dispatchEvent(new Event("change"));
      const title = root.querySelector('input[aria-label="新 MOC 名称"]');
      title.value = "人工输入的分类名";
      title.dispatchEvent(new Event("input"));
      const topic = root.querySelector(
        'textarea[aria-label="新 MOC 主题范围"]',
      );
      topic.value = "保留输入内容";
      topic.dispatchEvent(new Event("input"));
      render();
      if (
        !root.querySelector('[data-view-key="maintenance-preparation"]').open ||
        !root.querySelector('[data-view-key="maintenance-new-moc"]').open
      )
        throw Error("disclosure collapsed");
      if (
        root.querySelector('select[aria-label="整理模式"]').value !== "deep" ||
        root.querySelector('input[aria-label="新 MOC 名称"]').value !==
          "人工输入的分类名" ||
        root.querySelector("textarea").value !== "保留输入内容"
      )
        throw Error("draft lost");
      if (
        ![...root.querySelectorAll("button")].find(
          (b) => b.textContent === "确认并执行本次整理",
        ).disabled
      )
        throw Error("incomplete archive enabled");
      setLocale("en");
      if (
        !root.textContent.includes("Locally readable items") ||
        !root.textContent.includes(
          "Archiving requires checking all related links",
        ) ||
        !root.textContent.includes("Inbox/edited.md")
      )
        throw Error("coverage not bilingual");
      setLocale("zh-CN");
      let scans = 0;
      const view = createOrganizationView(
        {
          list: async () => {
            scans++;
            throw { status: 409, message: "目标 MOC 暂不可读" };
          },
          progress: async () => ({
            jobs: [job],
            package_scan: { state: "idle", items: [] },
          }),
        },
        () => ({ client_id: "c", vault_id: "v" }),
      );
      root.replaceChildren(view.host);
      await view.refresh();
      for (let i = 0; i < 5; i++) await view.refresh({ automatic: true });
      if (scans !== 1) throw Error("automatic catalog rescan");
      if (!root.querySelector('[data-maintenance-job="synthetic"]'))
        throw Error("preview hidden by catalog failure");
      view.clear();
      const { createView } = await import("/atlas/view_graph.js");
      const atlas = createView({
        api: {
          graph: {
            policy: async () => ({
              enabled: true,
              configured: true,
              value: { engine: { model: { name: "synthetic" } } },
            }),
            notes: async () => ({
              items: [
                {
                  note_id: "n",
                  title: "当前人工修订版",
                  note_path: "Inbox/edited-current.md",
                  sha256: "new",
                  selectable: true,
                },
              ],
              coverage,
            }),
            suggestions: async () => ({ items: [] }),
            jobs: async () => [],
            intents: async () => [],
          },
          organization: {
            list: async () => ({
              jobs: [],
              items: [],
              mocs: [],
              packages: [],
              coverage,
              total: 0,
              next_offset: null,
              policy: {
                settings: {
                  mode: "suggest",
                  include: [],
                  exclude: [],
                  conditions: [],
                  rules: [],
                  interval_seconds: 60,
                  match: "all",
                },
              },
            }),
            progress: async () => ({ jobs: [] }),
          },
        },
        store: {
          clients: async () => [
            {
              id: "c",
              name: "隔离身份",
              active: true,
              grants: [{ vault_id: "v", can_query: true }],
            },
          ],
          vaults: async () => [{ id: "v", name: "隔离库", status: "active" }],
        },
        navigate: () => {
          throw Error("unexpected navigation");
        },
      });
      root.replaceChildren(atlas.node);
      await atlas.refresh();
      const check = root.querySelector('.a-note-picks input[type="checkbox"]');
      if (
        !check ||
        check.disabled ||
        !root.textContent.includes("当前人工修订版") ||
        !root.textContent.includes("Inbox/edited.md")
      )
        throw Error("Atlas partial selector failed");
      atlas.dispose();
      render();
      return {
        preserved_disclosure_and_draft: true,
        bilingual_coverage: true,
        incomplete_archive_disabled: true,
        catalog_reads: scans,
        preview_retained: true,
        atlas_partial_selector: true,
      };
    });
    assert.deepEqual(errors, []);
    if (out) {
      fs.mkdirSync(out, { recursive: true });
      await page.screenshot({
        path: path.join(out, "partial-preview-components.png"),
        fullPage: true,
      });
      fs.writeFileSync(
        path.join(out, "component-checkpoint.json"),
        JSON.stringify({ state: "PASS", ...result }, null, 2),
      );
    }
    console.log(JSON.stringify({ state: "PASS", ...result }));
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
