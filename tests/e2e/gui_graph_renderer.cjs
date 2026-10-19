// Actual locked Sigma renderer with synthetic nodes; no analysis or Vault writes.
const { chromium } = require("playwright"),
  assert = require("node:assert/strict"),
  fs = require("node:fs");
const base = process.env.FIBER_GUI_ACCEPTANCE_URL,
  out = process.env.FIBER_GUI_EVIDENCE;
assert(base && out);
const u = new URL(base);
assert(u.hostname === "127.0.0.1" && +u.port >= 18000 && +u.port <= 19000);
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch({
      args: ["--enable-unsafe-swiftshader"],
    }),
    page = await browser.newPage({ viewport: { width: 1180, height: 820 } });
  await page.goto(base);
  await page.locator("#password").fill("synthetic-gui-acceptance");
  await page.locator("#login-form button").click();
  await page.locator("#home-cost-title").waitFor();
  const initial = await page.evaluate(async () => {
    const { createGraphRenderer } =
      await import("/static/graph_renderer.bundle.js");
    const container = document.createElement("div");
    container.id = "renderer-fixture";
    container.style.cssText =
      "width:500px;height:320px;position:fixed;right:20px;bottom:20px;background:var(--surface-base);z-index:40";
    document.body.append(container);
    const nodes = ["a", "b", "c"].map((id) => ({
        id,
        kind: "note",
        label: id,
      })),
      edges = [
        { id: "e1", source: "a", target: "b", kind: "explicit" },
        { id: "e2", source: "b", target: "c", kind: "accepted" },
        { id: "e3", source: "a", target: "c", kind: "suggested" },
      ];
    const r = createGraphRenderer(
      container,
      { nodes, edges },
      {
        onSelect: () => {},
        onFailure: (message) => {
          throw new Error(message);
        },
        reducedMotion: true,
      },
    );
    window.__migrationRenderer = r;
    r.focus("b");
    r.graph.setNodeAttribute("b", "fixed", true);
    return r.renderer.getCamera().getState();
  });
  for (const theme of ["cursor", "oracle", "tesla"]) {
    await page.locator("#theme-toggle").click();
    await page.locator(`[data-theme-choice="${theme}"]`).click();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    const state = await page.evaluate(() => ({
      camera: window.__migrationRenderer.renderer.getCamera().getState(),
      fixed: window.__migrationRenderer.graph.getNodeAttribute("b", "fixed"),
      selected:
        window.__migrationRenderer.renderer.getNodeDisplayData("b").highlighted,
      widths: ["e3", "e1", "e2"].map((id) =>
        window.__migrationRenderer.graph.getEdgeAttribute(id, "size"),
      ),
    }));
    assert.deepEqual(state.camera, initial);
    assert(state.fixed);
    assert(state.selected);
    assert(
      state.widths[0] < state.widths[1] && state.widths[1] < state.widths[2],
    );
    await page.screenshot({ path: out + `/renderer-${theme}.png` });
  }
  await page.evaluate(() => window.__migrationRenderer.kill());
  await browser.close();
  fs.writeFileSync(
    out + "/result.json",
    JSON.stringify(
      {
        actual_webgl: true,
        synthetic: true,
        camera_selection_pin_preserved: true,
        three_theme: true,
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS: actual renderer, three theme, camera/fixed node preserved",
  );
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
