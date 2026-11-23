import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { installDOM } from "./reliability_dom_fixture.js";
import {
  createIndexActivity,
  positionFooterIndexActivity,
} from "../../src/fiber_bundle/web/index_activity.js";

test("classic footer CSS opens upward at sidebar width while the Atlas dropdown stays unchanged", async () => {
  const css = await readFile(
    new URL("../../src/fiber_bundle/web/index_activity.css", import.meta.url),
    "utf8",
  );
  const rule = (selector) =>
    css.slice(
      css.indexOf(selector + " {"),
      css.indexOf("}", css.indexOf(selector + " {")),
    );
  assert.match(rule(".index-activity-panel"), /top: calc\(100% \+ 8px\)/);
  assert.match(
    rule(".index-activity-panel"),
    /width: min\(370px, calc\(100vw - 28px\)\)/,
  );
  assert.match(rule(".sidebar-footer .index-activity-panel"), /top: auto/);
  assert.match(
    rule(".sidebar-footer .index-activity-panel"),
    /bottom: calc\(100% \+ 8px\)/,
  );
  assert.match(rule(".sidebar-footer .index-activity-panel"), /left: 0/);
  assert.match(rule(".sidebar-footer .index-activity-panel"), /width: 100%/);
  assert.match(rule(".index-activity-panel"), /overflow-y: auto/);
});

test("footer menu uses actual available height across desktop, short and narrow windows", () => {
  const dom = installDOM();
  try {
    const sidebar = dom.document.createElement("aside"),
      footer = dom.document.createElement("div");
    sidebar.className = "sidebar";
    footer.className = "sidebar-footer";
    dom.document.body.append(sidebar);
    sidebar.append(footer);
    const view = createIndexActivity({ read: async () => ({}), onOpen() {} });
    footer.append(view.node);
    const [summary, panel] = view.node.children;
    for (const [height, top, sidebarTop] of [
      [720, 564, 80],
      [360, 210, 90],
      [900, 744, 80],
      [480, 188, 124],
    ]) {
      window.innerHeight = height;
      summary.getBoundingClientRect = () => ({ top });
      sidebar.getBoundingClientRect = () => ({ top: sidebarTop });
      view.node.open = true;
      view.node.listeners.toggle();
      const maximum = Number.parseInt(panel.style.maxHeight, 10);
      assert(maximum >= 0 && maximum <= top - sidebarTop - 16);
      assert(maximum <= height * 0.65);
      // Moving into the top bar for mobile must release the footer-only bound.
      dom.document.body.append(view.node);
      positionFooterIndexActivity(view.node, summary, panel);
      assert.equal(panel.style.maxHeight, "");
      footer.append(view.node);
    }
    view.dispose();
  } finally {
    dom.restore();
  }
});
