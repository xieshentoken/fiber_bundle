import { h } from "./dom.js";
import { card, notice } from "./ui.js";
import { lang } from "./i18n.js";
import { featureControls } from "./feature_controls.js";
import { createVaultRelocationView } from "../vault_relocation.js";
export function create(ctx, params) {
  const host = h("div", { hidden: true }),
    controls = featureControls(host),
    node = h(
      "div.a-stack",
      card(
        { title: lang() === "en" ? "Move vault location" : "迁移仓库位置" },
        notice(
          lang() === "en"
            ? "Move on this Mac within the same filesystem. Vault identity, history, manual MOC and channel bindings are preserved."
            : "本机同盘迁移；保留知识库身份、历史、人工 MOC 与消息绑定。",
          "info",
        ),
        controls.node,
      ),
    );
  let controller,
    epoch = 0;
  return {
    node,
    async refresh() {
      const mine = ++epoch;
      const vault = (await ctx.store.vaults({ force: true })).find(
        (v) => v.id === params.vault,
      );
      if (mine !== epoch) return;
      if (!vault)
        throw Error(lang() === "en" ? "Vault unavailable" : "知识库不可用");
      controller ??= createVaultRelocationView(
        ctx.api.vaultRelocations,
        host,
        () => ctx.navigate("vaults", "detail", { vault: vault.id }),
      );
      await controller.open(vault);
    },
    dispose() {
      epoch++;
      controller?.clear();
      controls.dispose();
    },
  };
}
