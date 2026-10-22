import { h } from "./dom.js";
import { card, disclosure } from "./ui.js";
import { lang } from "./i18n.js";
import { featureControls } from "./feature_controls.js";
import { createBackupAutomation } from "../backup_automation.js";
import { createView as offlineView } from "./view_backup_offline.js";
export function createView(ctx) {
  const root = h("div"),
    controls = featureControls(root),
    online = createBackupAutomation(ctx.api, root),
    offline = offlineView(ctx);
  return {
    node: h(
      "div.a-stack",
      card(
        { title: lang() === "en" ? "Encrypted backups" : "加密备份" },
        controls.node,
      ),
      card(
        {},
        disclosure(
          lang() === "en" ? "Offline backup & restore" : "离线备份与恢复",
          h("div.a-toolbar", ...(offline.header?.actions || [])),
          offline.node,
        ),
      ),
    ),
    async refresh() {
      await Promise.all([online.refresh(), offline.refresh()]);
    },
    dispose() {
      online.clear();
      offline.dispose?.();
      controls.dispose();
    },
  };
}
