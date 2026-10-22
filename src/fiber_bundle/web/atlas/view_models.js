import { h } from "./dom.js";
import { card, disclosure } from "./ui.js";
import { lang } from "./i18n.js";
import { featureControls } from "./feature_controls.js";
import { createModelCatalog } from "../models_catalog.js";
import { createView as routingView } from "./view_model_routes.js";
export function createView(ctx) {
  const root = h("div", { id: "models-catalog" }),
    controls = featureControls(root),
    routing = routingView(ctx);
  let catalog;
  return {
    node: h(
      "div.a-stack",
      card(
        {
          title:
            lang() === "en"
              ? "Models, API keys & usage budgets"
              : "模型、API Key 与统一消耗设置",
        },
        controls.node,
      ),
      card(
        {},
        disclosure(
          lang() === "en" ? "Advanced execution routing" : "高级执行路由",
          routing.node,
        ),
      ),
    ),
    async refresh() {
      catalog ??= createModelCatalog(ctx.api);
      await Promise.all([catalog.refresh(), routing.refresh()]);
    },
    dirty: () => !!catalog?.dirty,
    dispose() {
      catalog?.clear();
      routing.dispose?.();
      controls.dispose();
    },
  };
}
