import { h } from "./dom.js";
import { featureControls } from "./feature_controls.js";
import { createProjectSkillsView } from "../project_skills.js";
import { createVideoPanel } from "../video_processing.js";
export async function mountInventoryExtension(ctx, name, host, params = {}) {
  const root = h("div", name === "video" ? { id: "video-processing" } : {}),
    controls = featureControls(root);
  host.replaceChildren(controls.node);
  const view =
    name === "video"
      ? createVideoPanel(ctx.api)
      : createProjectSkillsView(ctx.api, () => root);
  await view.refresh();
  if (name === "video" && params.client && params.vault)
    await view.selectScope(params.client, params.vault);
  return {
    dispose() {
      view.clear();
      controls.dispose();
    },
  };
}
