// Atlas owns the page; the current domain controller owns policy and explicit task actions.
import { h } from "./dom.js";
import { card } from "./ui.js";
import { lang } from "./i18n.js";
import { featureControls } from "./feature_controls.js";
import { automaticSettings } from "../automatic_settings.js";
import { hasUnsavedChanges } from "../unsaved_changes.js";
export function create(ctx, vault) {
  const host = h("div"),
    controls = featureControls(host);
  return {
    node: card(
      {
        title:
          lang() === "en"
            ? "Processing, review & exceptions"
            : "处理、审核与异常任务",
      },
      controls.node,
    ),
    async refresh() {
      const panel = automaticSettings(vault, ctx.api);
      host.replaceChildren(panel);
      await panel.openTasks();
    },
    dirty: () => hasUnsavedChanges(host),
    dispose() {
      host.replaceChildren();
      controls.dispose();
    },
  };
}
